"""Read network state from a host via iproute2's JSON output."""

from __future__ import annotations

import json
import re

from netgrip.core.model import (
    Address,
    Container,
    DockerNetwork,
    Gateway,
    Interface,
    PortMapping,
    WgPeer,
)
from netgrip.core.runner import Runner

PROBE_COMMAND = ["ip", "-details", "-json", "address", "show"]
# Per-port VLAN membership on vlan-aware bridges (Proxmox and friends). A bonus
# read: old `bridge` lacks `-json` and plain bridges return nothing useful, so a
# failure here just leaves the tags blank.
BRIDGE_VLAN_COMMAND = ["bridge", "-json", "vlan", "show"]
# Wireless detection: a Wi-Fi netdev carries a `phy80211` symlink under its
# sysfs directory. List the interfaces that have one in a single read; on a host
# without these entries (or a non-Linux target) nothing prints and every
# interface stays wired — the safe default. Qt-free and works over SSH.
WIRELESS_COMMAND = [
    "sh", "-c",
    'for d in /sys/class/net/*/phy80211; do '
    '[ -e "$d" ] && basename "${d%/phy80211}"; done',
]
# Default routes are read per family so an interface can carry both an IPv4 and
# an IPv6 default at once (each belongs to its own protocol box).
ROUTE_COMMANDS = {
    4: ["ip", "-json", "-4", "route", "show", "default"],
    6: ["ip", "-json", "-6", "route", "show", "default"],
}
# One round trip for DNS: capability marker, then resolv.conf (the effective,
# host-wide list), then resolvectl's per-link servers and search domains so we
# can show where each resolver comes from. Sections are separated by markers.
#
# Every piece is best-effort, so the script ends with `exit 0`: on a host
# without systemd-resolved, `resolvectl` is "command not found" (exit 127) and
# would otherwise fail the whole read — discarding the resolv.conf we just read
# and leaving DNS blank, even though resolv.conf was there all along.
_LINKDNS = "@@LINKDNS@@"
_LINKDOMAIN = "@@LINKDOMAIN@@"
DNS_COMMAND = [
    "sh", "-c",
    "command -v resolvectl >/dev/null 2>&1 && echo yes || echo no; "
    "cat /etc/resolv.conf 2>/dev/null; "
    f"echo {_LINKDNS}; resolvectl dns 2>/dev/null; "
    f"echo {_LINKDOMAIN}; resolvectl domain 2>/dev/null; "
    "exit 0",
]

# `resolvectl dns` / `domain` print one line per link: "Link 2 (eth0): a b c".
_RESOLVECTL_LINK_RE = re.compile(r"^Link\s+\d+\s+\(([^)]+)\):\s*(.*)$")

# Docker, read best-effort and unprivileged. `network ls -q` feeds the bridge
# names / subnets read; `ps -q | xargs docker inspect` reads each running
# container (image, compose labels, per-network IP, published ports). Both pipe
# through `sh -c` like the DNS read; if docker is missing or the daemon is
# unreachable the command errors and the caller falls back to "no docker" — it
# never fails the rest of the probe. `xargs -r` skips the inspect when there are
# no running containers (an empty `docker inspect` would otherwise error).
DOCKER_NETWORK_COMMAND = [
    "sh", "-c", "docker network inspect $(docker network ls -q) 2>/dev/null",
]
DOCKER_CONTAINER_COMMAND = [
    "sh", "-c", "docker ps -q | xargs -r docker inspect 2>/dev/null",
]

# Docker labels carrying the compose project / service of a container.
_COMPOSE_PROJECT = "com.docker.compose.project"
_COMPOSE_SERVICE = "com.docker.compose.service"
# Option naming the host bridge of a docker bridge network (e.g. "docker0").
_BRIDGE_NAME_OPT = "com.docker.network.bridge.name"
# A published port key, "80/tcp" or just "80" (proto defaults to tcp).
_PORT_KEY_RE = re.compile(r"^(\d+)(?:/(\w+))?$")

# Per-interface RX/TX byte counters. `ip -s` extends the link output with a
# `stats64` block (64-bit counters available since kernel 2.6.36); the older
# 32-bit `stats` block is the fallback for rare cases where stats64 is absent.
STATS_COMMAND = ["ip", "-s", "-j", "link", "show"]

# Routing protocols that mean "the kernel/DHCP/RA put this here", not the user.
_DYNAMIC_PROTOCOLS = {"dhcp", "ra", "redirect", "kernel"}

# WireGuard peer dump. Requires CAP_NET_ADMIN; degrades silently when
# unprivileged or when the `wg` tool is absent. First line is the interface's
# own row (private-key, public-key, listen-port, fwmark); subsequent lines are
# peers (tab-separated: public-key, preshared-key, endpoint, allowed-ips,
# latest-handshake, rx-bytes, tx-bytes, keepalive).
_WG_DUMP_TMPL = ["wg", "show", "{dev}", "dump"]


def probe(runner: Runner) -> list[Interface]:
    out = runner.run(PROBE_COMMAND)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse iproute2 JSON output from '{runner.label}'. "
            "netgrip needs iproute2 4.14 or newer on the managed host."
        ) from exc
    interfaces = parse_addr_json(payload)
    _enrich_gateways(runner, interfaces)
    _enrich_bridge_vlans(runner, interfaces)
    _enrich_wireless(runner, interfaces)
    _enrich_stats(runner, interfaces)
    _enrich_wg_peers(runner, interfaces)
    return interfaces


def probe_dns(
    runner: Runner,
) -> tuple[list[str], list[str], bool, dict[str, tuple[list[str], list[str]]]]:
    """Return (effective nameservers, search domains, can_edit_dns, per-link).

    ``per-link`` maps an interface name to ``(servers, search)`` as configured
    on that link via systemd-resolved; it is empty on hosts without resolvectl.

    Best-effort: a host without resolv.conf or one we can't read just yields
    empty values, never an error — DNS visibility is a convenience.
    """
    try:
        out = runner.run(DNS_COMMAND)
    except (RuntimeError, ValueError):
        return [], [], False, {}
    resolv_part, _, rest = out.partition(_LINKDNS)
    linkdns_part, _, linkdomain_part = rest.partition(_LINKDOMAIN)
    lines = resolv_part.splitlines()
    can_edit = bool(lines) and lines[0].strip() == "yes"
    servers, search = parse_resolv_conf("\n".join(lines[1:]))
    link_dns = parse_resolvectl_links(linkdns_part)
    link_domain = parse_resolvectl_links(linkdomain_part)
    per_link = {
        name: (link_dns.get(name, []), link_domain.get(name, []))
        for name in set(link_dns) | set(link_domain)
    }
    return servers, search, can_edit, per_link


def apply_link_dns(
    interfaces: list[Interface], per_link: dict[str, tuple[list[str], list[str]]]
) -> None:
    """Attach per-link DNS (from :func:`probe_dns`) onto the interfaces.

    The "(dhcp)" tag is heuristic: resolvectl does not report whether a link's
    DNS was learned dynamically, so we treat it as dynamic when the link also
    carries a dynamic address or default route — the usual DHCP/RA case.
    """
    for iface in interfaces:
        servers, search = per_link.get(iface.name, ([], []))
        iface.dns = list(servers)
        # A leading '~' marks a routing-only domain in resolvectl; drop it.
        iface.dns_search = [d.lstrip("~") for d in search]
        iface.dns_dynamic = bool(servers) and (
            any(a.dynamic for a in iface.addresses)
            or any(g.dynamic for g in iface.gateways.values())
        )


def _enrich_gateways(runner: Runner, interfaces: list[Interface]) -> None:
    by_name = {i.name: i for i in interfaces}
    for family, command in ROUTE_COMMANDS.items():
        try:
            routes = json.loads(runner.run(command))
        except (RuntimeError, ValueError):
            continue  # routing info is a bonus; never fail the probe over it
        for dev, gateway in parse_route_json(routes).items():
            iface = by_name.get(dev)
            if iface is not None:
                iface.gateways[family] = gateway


def _enrich_bridge_vlans(runner: Runner, interfaces: list[Interface]) -> None:
    try:
        payload = json.loads(runner.run(BRIDGE_VLAN_COMMAND))
    except (RuntimeError, ValueError):
        return  # vlan-filtering info is a bonus; never fail the probe over it
    if not isinstance(payload, list):
        return
    by_name = {i.name: i for i in interfaces}
    for name, (pvid, tagged) in parse_bridge_vlan_json(payload).items():
        iface = by_name.get(name)
        if iface is not None:
            iface.pvid = pvid
            iface.vlan_tags = tagged


def _enrich_wireless(runner: Runner, interfaces: list[Interface]) -> None:
    """Mark physical NICs backed by an 802.11 device (a phy80211 in sysfs).

    Best-effort: a host without those sysfs entries (or a non-Linux target)
    yields no names and every interface stays wired — never fail over it."""
    try:
        out = runner.run(WIRELESS_COMMAND)
    except (RuntimeError, ValueError):
        return  # wireless detection is a bonus; never fail the probe over it
    wireless = parse_wireless(out)
    for iface in interfaces:
        iface.wireless = iface.name in wireless


def _enrich_stats(runner: Runner, interfaces: list[Interface]) -> None:
    """Populate rx_bytes/tx_bytes on each interface from `ip -s link show`.

    Best-effort: a failure here (old iproute2, remote host quirk) leaves
    rx_bytes/tx_bytes at zero and never fails the rest of the probe."""
    try:
        payload = json.loads(runner.run(STATS_COMMAND))
    except (RuntimeError, ValueError):
        return
    if not isinstance(payload, list):
        return
    by_name = {i.name: i for i in interfaces}
    for name, rx, tx in parse_stats_json(payload):
        iface = by_name.get(name)
        if iface is not None:
            iface.rx_bytes = rx
            iface.tx_bytes = tx


def _enrich_wg_peers(runner: Runner, interfaces: list[Interface]) -> None:
    """Populate wg_peers on every wireguard interface via `wg show <dev> dump`.

    Requires CAP_NET_ADMIN / root. Degrades silently when the `wg` tool is
    absent, the interface is gone, or we lack privilege — the interface box
    still renders, just without peer detail. For each peer whose endpoint IP
    can be resolved, we also run `ip route get` to find which NIC currently
    carries that traffic and tag it on the peer as egress_dev/egress_src.
    """
    for iface in interfaces:
        if iface.kind != "wireguard":
            continue
        cmd = [part.replace("{dev}", iface.name) for part in _WG_DUMP_TMPL]
        try:
            text = runner.run(cmd)
        except (RuntimeError, ValueError):
            continue  # wg missing, EPERM, or interface gone — silent degrade
        iface.wg_peers = parse_wg_dump(text)

    # Egress-route probe: one `ip route get` per peer that has an endpoint.
    for iface in interfaces:
        for peer in iface.wg_peers:
            ep_ip = _endpoint_ip(peer.endpoint)
            if not ep_ip:
                continue
            try:
                out = runner.run(["ip", "-json", "route", "get", ep_ip])
                routes = json.loads(out)
            except (RuntimeError, ValueError):
                continue
            if not isinstance(routes, list) or not routes:
                continue
            r = routes[0]
            peer.egress_dev = r.get("dev") or None
            peer.egress_src = r.get("prefsrc") or None


def parse_wg_dump(text: str) -> list[WgPeer]:
    """Parse `wg show <dev> dump` output into :class:`WgPeer` objects.

    The first line is the interface's own row and is skipped. Each subsequent
    line is a peer: eight tab-separated fields — public-key, preshared-key,
    endpoint, allowed-ips (comma-separated CIDRs), latest-handshake (unix
    timestamp), transfer-rx, transfer-tx, persistent-keepalive. Malformed
    lines are skipped silently so a partial output still yields clean peers.
    """
    peers: list[WgPeer] = []
    lines = text.strip().splitlines()
    for line in lines[1:]:  # skip the interface row
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        pub_key = parts[0]
        endpoint_raw = parts[2]
        endpoint = "" if endpoint_raw == "(none)" else endpoint_raw
        allowed_raw = parts[3]
        allowed_ips = [a for a in allowed_raw.split(",") if a and a != "(none)"]
        try:
            latest_handshake = int(parts[4])
            rx_bytes = int(parts[5])
            tx_bytes = int(parts[6])
        except (ValueError, IndexError):
            latest_handshake = rx_bytes = tx_bytes = 0
        peers.append(WgPeer(
            public_key=pub_key,
            endpoint=endpoint,
            allowed_ips=allowed_ips,
            latest_handshake=latest_handshake,
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
        ))
    return peers


def _endpoint_ip(endpoint: str) -> str | None:
    """Extract the bare IP from an endpoint string like '1.2.3.4:51820' or '[::1]:51820'."""
    if not endpoint:
        return None
    if endpoint.startswith("["):  # IPv6 bracket notation: [addr]:port
        end = endpoint.find("]")
        return endpoint[1:end] if end > 1 else None
    colon = endpoint.rfind(":")
    return endpoint[:colon] if colon > 0 else None


def parse_wireless(text: str) -> set[str]:
    """Interface names with an 802.11 phy, one per line from `WIRELESS_COMMAND`."""
    return {line.strip() for line in text.splitlines() if line.strip()}


def parse_stats_json(payload: list[dict]) -> list[tuple[str, int, int]]:
    """Extract (ifname, rx_bytes, tx_bytes) from `ip -s -j link show` output.

    Prefers the 64-bit ``stats64`` block; falls back to ``stats`` (32-bit) for
    kernels that lack it. Returns zero for either counter when the block is absent.
    """
    result: list[tuple[str, int, int]] = []
    for item in payload:
        name = item.get("ifname")
        if not name:
            continue
        stats = item.get("stats64") or item.get("stats") or {}
        rx = int((stats.get("rx") or {}).get("bytes") or 0)
        tx = int((stats.get("tx") or {}).get("bytes") or 0)
        result.append((name, rx, tx))
    return result


def parse_bridge_vlan_json(payload: list[dict]) -> dict[str, tuple[int | None, list[str]]]:
    """Map port -> (pvid, tagged VLANs) from `bridge -json vlan show`.

    ``pvid`` is the port's untagged native VLAN; ``tagged`` lists the VLANs it
    carries with an 802.1q tag (the native, egress-untagged VLAN is excluded).
    A VLAN range comes through as one ``"100-200"`` token.
    """
    table: dict[str, tuple[int | None, list[str]]] = {}
    for entry in payload:
        name = entry.get("ifname")
        if not name:
            continue
        pvid: int | None = None
        tagged: list[str] = []
        for vlan in entry.get("vlans") or []:
            vid = vlan.get("vlan")
            if vid is None:
                continue
            flags = vlan.get("flags") or []
            if "PVID" in flags:
                pvid = vid
            if "Egress Untagged" not in flags:
                end = vlan.get("vlanEnd")
                tagged.append(f"{vid}-{end}" if end else str(vid))
        table[name] = (pvid, tagged)
    return table


def probe_docker(runner: Runner) -> tuple[list[DockerNetwork], list[Container]]:
    """Read the host's docker networks and running containers.

    Best-effort and unprivileged: a host without docker (or where the user can't
    reach the daemon) yields ``([], [])`` and never raises, so docker visibility
    is a pure bonus on top of the iproute2 probe.
    """
    networks = _run_docker_json(runner, DOCKER_NETWORK_COMMAND, parse_docker_networks)
    containers = _run_docker_json(runner, DOCKER_CONTAINER_COMMAND, parse_docker_containers)
    return networks, containers


def _run_docker_json(runner: Runner, command: list[str], parse):
    try:
        payload = json.loads(runner.run(command))
    except (RuntimeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return parse(payload)


def apply_docker(interfaces: list[Interface], networks: list[DockerNetwork]) -> None:
    """Tag each bridge Interface with the docker network it backs, so its box can
    name the network (the bridge already shows from the iproute2 probe)."""
    by_name = {i.name: i for i in interfaces}
    for net in networks:
        iface = by_name.get(net.bridge or "")
        if iface is not None:
            iface.docker_network = net.name


def parse_docker_networks(payload: list[dict]) -> list[DockerNetwork]:
    """Parse `docker network inspect` output into :class:`DockerNetwork`s.

    The host bridge ifname comes from the bridge-name option; a user bridge
    network that didn't set one defaults to ``br-<id12>``, the default ``bridge``
    network to ``docker0``. Non-bridge drivers (host/overlay/macvlan/null) carry
    no host bridge and just record their name and driver.
    """
    networks: list[DockerNetwork] = []
    for entry in payload:
        name = entry.get("Name")
        if not name:
            continue
        nid = entry.get("Id") or ""
        driver = entry.get("Driver") or "bridge"
        options = entry.get("Options") or {}
        bridge = options.get(_BRIDGE_NAME_OPT)
        if not bridge and driver == "bridge":
            bridge = "docker0" if name == "bridge" else f"br-{nid[:12]}"
        subnets: list[str] = []
        gateway: str | None = None
        for cfg in (entry.get("IPAM") or {}).get("Config") or []:
            if cfg.get("Subnet"):
                subnets.append(cfg["Subnet"])
            if cfg.get("Gateway") and gateway is None:
                gateway = cfg["Gateway"]
        networks.append(DockerNetwork(
            name=name, id=nid, driver=driver,
            bridge=bridge if driver == "bridge" else None,
            subnets=subnets, gateway=gateway,
        ))
    return networks


def parse_docker_containers(payload: list[dict]) -> list[Container]:
    """Parse `docker inspect <containers>` output into :class:`Container`s."""
    containers: list[Container] = []
    for entry in payload:
        name = (entry.get("Name") or "").lstrip("/")
        if not name:
            continue
        config = entry.get("Config") or {}
        labels = config.get("Labels") or {}
        netsettings = entry.get("NetworkSettings") or {}
        networks = {
            net: data.get("IPAddress", "")
            for net, data in (netsettings.get("Networks") or {}).items()
            if isinstance(data, dict)
        }
        host_config = entry.get("HostConfig") or {}
        containers.append(Container(
            name=name,
            id=(entry.get("Id") or "")[:12],
            image=config.get("Image") or "",
            state=(entry.get("State") or {}).get("Status") or "running",
            compose_project=labels.get(_COMPOSE_PROJECT, ""),
            compose_service=labels.get(_COMPOSE_SERVICE, ""),
            networks={k: v for k, v in networks.items() if v},
            ports=parse_port_bindings(netsettings.get("Ports") or {}),
            network_mode=(host_config.get("NetworkMode") or "").lower(),
        ))
    return containers


def parse_port_bindings(ports: dict) -> list[PortMapping]:
    """Parse a container's `.NetworkSettings.Ports` into :class:`PortMapping`s.

    The map is ``{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}], ...}``;
    a ``null`` value means the port is exposed but not published, and is skipped.
    Bindings that differ only by an all-addresses host IP (the ``0.0.0.0`` and
    ``::`` pair Docker emits for one publish) collapse to a single mapping.
    """
    mappings: list[PortMapping] = []
    seen: set[tuple[str, int, int, str]] = set()
    for key, bindings in ports.items():
        if not bindings:
            continue
        match = _PORT_KEY_RE.match(key)
        if not match:
            continue
        container_port = int(match.group(1))
        protocol = match.group(2) or "tcp"
        for binding in bindings:
            host_ip = binding.get("HostIp") or ""
            try:
                host_port = int(binding.get("HostPort") or 0)
            except (TypeError, ValueError):
                continue
            if not host_port:
                continue
            norm_ip = "" if host_ip in ("", "0.0.0.0", "::") else host_ip
            dedupe = (norm_ip, host_port, container_port, protocol)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            mappings.append(PortMapping(host_ip, host_port, container_port, protocol))
    return mappings


def parse_route_json(payload: list[dict]) -> dict[str, Gateway]:
    """Map dev -> default `Gateway` from one family's `ip -json route show`."""
    gateways: dict[str, Gateway] = {}
    for route in payload:
        if route.get("dst") != "default":
            continue
        gw, dev = route.get("gateway"), route.get("dev")
        if not gw or not dev or dev in gateways:
            continue
        dynamic = route.get("protocol") in _DYNAMIC_PROTOCOLS
        gateways[dev] = Gateway(gw, dynamic)
    return gateways


def parse_resolvectl_links(text: str) -> dict[str, list[str]]:
    """Parse `resolvectl dns` / `resolvectl domain` into {link: [values]}."""
    links: dict[str, list[str]] = {}
    for raw in text.splitlines():
        match = _RESOLVECTL_LINK_RE.match(raw.strip())
        if match:
            links[match.group(1)] = match.group(2).split()
    return links


def parse_resolv_conf(text: str) -> tuple[list[str], list[str]]:
    """Pull nameservers and search domains out of resolv.conf text."""
    servers: list[str] = []
    search: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        parts = line.split()
        if parts[0] == "nameserver" and len(parts) >= 2:
            if parts[1] not in servers:
                servers.append(parts[1])
        elif parts[0] in ("search", "domain"):
            for domain in parts[1:]:
                if domain not in search:
                    search.append(domain)
    return servers, search


def parse_addr_json(payload: list[dict]) -> list[Interface]:
    """Turn `ip -details -json address show` output into model objects."""
    interfaces: list[Interface] = []
    # A veth's other end comes from IFLA_LINK: iproute2 reports it as a name
    # ("link") when the peer is in this namespace, or only as an ifindex
    # ("link_index") when it lives in another (e.g. a container). A peer in
    # another namespace also carries "link_netnsid"; its ifindex is meaningful
    # only in *that* namespace, so it must never be matched against our own
    # ifindexes (a container's eth0 ifindex routinely collides with a host
    # interface's, which would mis-pair every container veth to, say, eth0).
    # Stash all three and resolve once every link has been read.
    veth_peers: dict[str, tuple[str | None, int | None, int | None]] = {}
    for item in payload:
        linkinfo = item.get("linkinfo") or {}
        info_data = linkinfo.get("info_data") or {}
        kind = linkinfo.get("info_kind")
        if item.get("link_type") == "loopback":
            kind = "loopback"
        elif not kind:
            kind = "physical"

        iface = Interface(
            name=item.get("ifname", "?"),
            index=item.get("ifindex", 0),
            kind=kind,
            state=_operstate(item),
            mac=item.get("address") or "",
            mtu=item.get("mtu", 0),
            alias=item.get("ifalias") or "",
            master=item.get("master"),
            vlan_id=info_data.get("id") if kind == "vlan" else None,
            vlan_parent=item.get("link") if kind == "vlan" else None,
            bond_mode=info_data.get("mode") if kind == "bond" else None,
            bridge_vlan_aware=bool(info_data.get("vlan_filtering")) if kind == "bridge" else False,
        )
        if kind == "veth":
            veth_peers[iface.name] = (
                item.get("link"), item.get("link_index"), item.get("link_netnsid")
            )

        for ai in item.get("addr_info", []):
            family = 4 if ai.get("family") == "inet" else 6
            scope = ai.get("scope", "global")
            local = ai.get("local")
            if not local:
                continue
            if family == 6 and scope == "link":
                continue  # fe80:: link-locals exist on every up interface; pure noise
            iface.addresses.append(
                Address(
                    address=local,
                    prefixlen=ai.get("prefixlen", 32 if family == 4 else 128),
                    family=family,
                    scope=scope,
                    dynamic=bool(ai.get("dynamic")),
                )
            )
        interfaces.append(iface)

    # Resolve veth peers now that every interface is known. Prefer the name the
    # kernel gave us; otherwise map the peer ifindex to a local interface, but
    # only when the peer is in *our* namespace (no link_netnsid) — a cross-netns
    # ifindex would otherwise mis-pair a container's veth to a host interface. A
    # peer in another namespace (a container) resolves to neither and stays
    # unpaired; its docker container, not the bare veth, is what we draw.
    by_index = {i.index: i for i in interfaces}
    names = {i.name for i in interfaces}
    for iface in interfaces:
        link_name, link_index, netnsid = veth_peers.get(iface.name, (None, None, None))
        if link_name in names:
            iface.peer = link_name
        elif netnsid is None and link_index in by_index:
            iface.peer = by_index[link_index].name
    return interfaces


def _operstate(item: dict) -> str:
    state = (item.get("operstate") or "").lower()
    if state == "unknown":
        # Loopback and some virtual devices report UNKNOWN; fall back to flags.
        return "up" if "UP" in (item.get("flags") or []) else "down"
    return state if state in ("up", "down") else "down"
