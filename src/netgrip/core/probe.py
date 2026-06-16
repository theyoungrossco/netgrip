"""Read network state from a host via iproute2's JSON output."""

from __future__ import annotations

import json
import re

from netgrip.core.model import Address, Gateway, Interface
from netgrip.core.runner import Runner

PROBE_COMMAND = ["ip", "-details", "-json", "address", "show"]
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

# Routing protocols that mean "the kernel/DHCP/RA put this here", not the user.
_DYNAMIC_PROTOCOLS = {"dhcp", "ra", "redirect", "kernel"}


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
    # ("link_index") when it lives in another (e.g. a container). Stash both
    # and resolve to a name once every link has been read.
    veth_peers: dict[str, tuple[str | None, int | None]] = {}
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
        )
        if kind == "veth":
            veth_peers[iface.name] = (item.get("link"), item.get("link_index"))

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
    # kernel gave us; otherwise map the peer ifindex to a local interface. A
    # peer in another namespace resolves to neither and is left unpaired.
    by_index = {i.index: i for i in interfaces}
    names = {i.name for i in interfaces}
    for iface in interfaces:
        link_name, link_index = veth_peers.get(iface.name, (None, None))
        if link_name in names:
            iface.peer = link_name
        elif link_index in by_index:
            iface.peer = by_index[link_index].name
    return interfaces


def _operstate(item: dict) -> str:
    state = (item.get("operstate") or "").lower()
    if state == "unknown":
        # Loopback and some virtual devices report UNKNOWN; fall back to flags.
        return "up" if "UP" in (item.get("flags") or []) else "down"
    return state if state in ("up", "down") else "down"
