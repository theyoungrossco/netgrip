"""Data model describing the network state of one host.

These classes are plain data carriers. They are produced by
:mod:`netgrip.core.probe` and consumed by the UI; they never talk to the
system themselves.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from netgrip.core.backends import Backend

# Interface kinds that an IP configuration or a VLAN can be attached to.
ATTACHABLE_KINDS = {"physical", "bond", "bridge", "team", "vlan", "loopback"}

# Interface kinds rendered as a "group" (several NICs joined together).
GROUP_KINDS = {"bond", "bridge", "team"}


def ip_family(addr: str) -> int | None:
    """4 or 6 for a bare IP address, or None if it isn't one.

    Used to bucket per-link DNS servers into the IPv4 / IPv6 protocol box that
    the family belongs to.
    """
    try:
        return ipaddress.ip_address(addr).version
    except ValueError:
        return None


@dataclass
class Address:
    address: str
    prefixlen: int
    family: int  # 4 or 6
    scope: str = "global"
    dynamic: bool = False  # installed by DHCP / RA rather than statically

    @property
    def cidr(self) -> str:
        return f"{self.address}/{self.prefixlen}"


@dataclass
class Gateway:
    """The default route for one address family on one interface.

    A default route is per-(interface, family): an interface can hold both an
    IPv4 and an IPv6 default at once, so the gateway belongs to the protocol
    box, not to the link as a whole.
    """

    address: str
    dynamic: bool = False  # the default route was installed by DHCP / RA


@dataclass
class PortMapping:
    """A published container port: a host bind forwarded to a container port.

    Docker DNATs ``host_ip:host_port`` to ``container_port`` (the container's own
    IP on its network). ``host_ip`` is ``0.0.0.0`` / ``::`` for "every host
    address" or a specific host IP when the publish was pinned to one.
    """

    host_ip: str
    host_port: int
    container_port: int
    protocol: str = "tcp"  # tcp | udp

    @property
    def all_host_ips(self) -> bool:
        return self.host_ip in ("", "0.0.0.0", "::")

    def label(self) -> str:
        """Compact `:8080→80/tcp` (host-IP-prefixed when pinned to one)."""
        host = "" if self.all_host_ips else self.host_ip
        return f"{host}:{self.host_port}→{self.container_port}/{self.protocol}"


@dataclass
class Container:
    """A (running) Docker container, as seen from the host.

    ``networks`` maps a docker network name to this container's IP on it (bare,
    no prefix). ``ports`` are its published host bindings (see PortMapping).
    ``network_mode`` is "host" when the container shares the host network
    namespace directly (no bridge, no docker-assigned IP)."""

    name: str
    id: str = ""  # short id
    image: str = ""
    state: str = "running"
    compose_project: str = ""  # com.docker.compose.project label ("" if none)
    compose_service: str = ""  # com.docker.compose.service label
    networks: dict[str, str] = field(default_factory=dict)
    ports: list[PortMapping] = field(default_factory=list)
    network_mode: str = ""  # "host" when container shares the host network namespace

    @property
    def composed(self) -> bool:
        return bool(self.compose_project)

    def label(self) -> str:
        """`project/service` when composed, else the bare container name."""
        if self.composed:
            return f"{self.compose_project}/{self.compose_service or self.name}"
        return self.name


@dataclass
class DockerNetwork:
    """A docker network and, for the bridge driver, the host bridge backing it.

    ``bridge`` is the Linux bridge ifname (``docker0`` for the default ``bridge``
    network, ``br-<id12>`` for user networks) — the link that already shows on
    the canvas, now tied back to its docker network.
    """

    name: str
    id: str = ""
    driver: str = "bridge"
    bridge: str | None = None
    subnets: list[str] = field(default_factory=list)
    gateway: str | None = None


@dataclass
class Interface:
    name: str
    index: int = 0
    kind: str = "physical"  # physical | loopback | vlan | bond | bridge | veth | ...
    state: str = "down"  # up | down
    mac: str = ""
    # A physical NIC backed by an 802.11 (Wi-Fi) device rather than Ethernet,
    # detected from its sysfs phy80211 link. Drives the wired/wireless glyph.
    wireless: bool = False
    mtu: int = 0
    alias: str = ""  # kernel ifalias: a human label, set with `ip link set dev X alias`
    master: str | None = None  # name of the bond/bridge this NIC is enslaved to
    vlan_id: int | None = None
    vlan_parent: str | None = None
    bond_mode: str | None = None
    # The other end of a veth pair, when both ends live in this namespace (the
    # Proxmox firewall fwln/fwpr case). A container's far end sits in its own
    # netns and is not visible here, so this stays None for those.
    peer: str | None = None
    # Bridge VLAN filtering (vlan-aware bridges, e.g. Proxmox), read from
    # `bridge vlan show`. On the bridge itself: whether it filters by VLAN. On a
    # member port: its untagged native VLAN (pvid) and the VLANs it carries
    # tagged. Tags are display tokens ("20", "100-200") since a port may trunk a
    # whole range.
    bridge_vlan_aware: bool = False
    pvid: int | None = None
    vlan_tags: list[str] = field(default_factory=list)
    # The docker network this bridge backs (``docker0`` / ``br-…``), set during
    # the docker enrichment so the bridge box can say which network it is.
    docker_network: str | None = None
    # Per-family default route, keyed by family (4 / 6). See `Gateway`.
    gateways: dict[int, Gateway] = field(default_factory=dict)
    # Per-link DNS, as configured on this interface (systemd-resolved). These
    # are read where resolvectl is present; on plain resolv.conf hosts they are
    # empty and only the host-wide list on `HostState` is known.
    dns: list[str] = field(default_factory=list)
    dns_search: list[str] = field(default_factory=list)
    dns_dynamic: bool = False  # link DNS was handed out by DHCP / RA
    addresses: list[Address] = field(default_factory=list)
    # Cumulative RX/TX counters from `ip -s link show` (bytes since last reset).
    # Zero when stats weren't read (remote probe without -s) or counter is genuinely zero.
    rx_bytes: int = 0
    tx_bytes: int = 0

    @property
    def is_up(self) -> bool:
        return self.state == "up"

    @property
    def is_group(self) -> bool:
        return self.kind in GROUP_KINDS

    @property
    def is_vm_tap(self) -> bool:
        """True for a KVM/QEMU tap port: a tun device enslaved to a bridge."""
        return self.kind == "tun" and self.master is not None

    def addresses_for(self, family: int) -> list[Address]:
        return [a for a in self.addresses if a.family == family]

    def families(self) -> list[int]:
        """Address families present on this link, IPv4 first, deduplicated."""
        return [f for f in (4, 6) if any(a.family == f for a in self.addresses)]

    def configured_families(self) -> list[int]:
        """Families with *any* config worth a box — an address, a default route,
        or per-link DNS. Used to decide which IPv4/IPv6 groups to draw, so a
        family keeps its box (and its gateway/DNS stay visible and editable)
        after its last address is removed, instead of silently vanishing."""
        return [
            f for f in (4, 6)
            if self.addresses_for(f) or self.gateway_for(f) or self.dns_for(f)
        ]

    def gateway_for(self, family: int) -> Gateway | None:
        return self.gateways.get(family)

    def dns_for(self, family: int) -> list[str]:
        """The link's DNS servers that belong to ``family`` (by server IP)."""
        return [s for s in self.dns if ip_family(s) == family]

    def uses_dhcp(self, family: int) -> bool:
        """Whether this link draws ``family`` from a DHCP/RA lease — it holds a
        dynamic address or a dynamic default route for the family."""
        gw = self.gateway_for(family)
        return any(a.dynamic for a in self.addresses_for(family)) or bool(gw and gw.dynamic)

    def dhcp_dns_for(self, family: int, host_dns: list[str]) -> list[str]:
        """Host-wide resolvers of ``family`` (from resolv.conf) inferred to come
        from *this* link's DHCP/RA lease, so they can be shown on its protocol
        box even where systemd-resolved doesn't attribute DNS per link.

        Returned only when the link uses DHCP for the family (see
        :meth:`uses_dhcp`), so static host-wide resolvers are never mis-pinned to
        it; empty when the link already has per-link DNS of its own (use
        :meth:`dns_for` then) or doesn't use DHCP for the family."""
        if self.dns_for(family) or not self.uses_dhcp(family):
            return []
        return [s for s in host_dns if ip_family(s) == family]


@dataclass
class NftRule:
    """One nftables rule, as read from `nft -j list ruleset`."""

    handle: int
    family: str
    table: str
    chain: str
    comment: str = ""
    # Interface names referenced by iifname/oifname match expressions.
    ifaces: list[str] = field(default_factory=list)
    # Compact human-readable rendering of the rule expression.
    expr_summary: str = ""


@dataclass
class NftChain:
    """One nftables chain (base or regular)."""

    name: str
    family: str
    table: str
    handle: int
    # Present only on base chains (hooked into the netfilter framework).
    chain_type: str | None = None   # filter | nat | route
    hook: str | None = None         # input | output | forward | prerouting | postrouting
    prio: int | None = None
    policy: str | None = None       # accept | drop
    rules: list[NftRule] = field(default_factory=list)

    @property
    def is_base_chain(self) -> bool:
        return self.hook is not None


@dataclass
class NftTable:
    """One nftables table."""

    name: str
    family: str
    handle: int
    chains: list[NftChain] = field(default_factory=list)


@dataclass
class FirewallState:
    """nftables ruleset snapshot, produced by probe_firewall().

    ``available`` is False when nft is absent or returned no parseable output;
    the canvas shows a "firewall not available" placeholder in that case."""

    tables: list[NftTable] = field(default_factory=list)
    available: bool = True

    def rules_for_iface(self, ifname: str) -> list[NftRule]:
        """All rules that reference ``ifname`` via iifname/oifname."""
        return [
            rule
            for table in self.tables
            for chain in table.chains
            for rule in chain.rules
            if ifname in rule.ifaces
        ]

    def chains_for_iface(self, ifname: str) -> list[tuple[NftTable, NftChain]]:
        """(table, chain) pairs that contain at least one rule referencing ``ifname``."""
        return [
            (table, chain)
            for table in self.tables
            for chain in table.chains
            if any(ifname in r.ifaces for r in chain.rules)
        ]


@dataclass
class HostState:
    """Snapshot of all interfaces on one host."""

    label: str
    interfaces: list[Interface] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)  # effective nameservers (resolv.conf)
    dns_search: list[str] = field(default_factory=list)  # search domains
    can_edit_dns: bool = False  # systemd-resolved (resolvectl) present for per-link DNS
    manual_dns: list[str] = field(default_factory=list)  # user-added extras (from store)
    backend: Backend | None = None  # which subsystem owns persistent config (see backends.py)
    # Docker view (best-effort; empty when docker is absent or unreachable). The
    # networks tie a bridge Interface back to its docker network; the containers
    # hang off those networks. See probe_docker / core/model PortMapping.
    docker_networks: list[DockerNetwork] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    # Firewall view (best-effort; FirewallState(available=False) when nft absent).
    firewall: FirewallState = field(default_factory=FirewallState)
    # (interface, family) pairs the user has switched to DHCP but not yet saved.
    # UI-only intent (set by main_window, redrawn each probe): the family still
    # holds its static address at runtime until Save writes `dhcp` through the
    # backend, so this is what keeps the box showing the pending switch. See M5.
    dhcp_pending: set[tuple[str, int]] = field(default_factory=set)
    # (interface, cidr) static addresses the user has deleted but not yet saved.
    # Like dhcp_pending, a UI-only intent: on a host whose backend re-asserts its
    # config (NetworkManager et al.) a runtime `ip addr del` just bounces back, so
    # the delete is deferred to Save and the box stays, flagged for removal.
    removed_pending: set[tuple[str, str]] = field(default_factory=set)
    # (interface, family) the user wants to stop taking DNS from the DHCP/RA
    # lease. There is no runtime command for "ignore the lease's DNS" — it is a
    # backend/profile setting — so like dhcp_pending it is a UI intent applied at
    # Save (LinkConfig.set_ignore_dhcp_dns), shown meanwhile as a box marker.
    dns_off_pending: set[tuple[str, int]] = field(default_factory=set)

    def get(self, name: str) -> Interface | None:
        return next((i for i in self.interfaces if i.name == name), None)

    def resolver_origin(self, server: str) -> str:
        """Where an effective resolver comes from: a link name, "manual", or
        "system" when nothing more specific is known (no systemd-resolved)."""
        for iface in self.interfaces:
            if server in iface.dns:
                return iface.name
        if server in self.manual_dns:
            return "manual"
        return "system"

    def members_of(self, group_name: str) -> list[Interface]:
        return [i for i in self.interfaces if i.master == group_name]

    def free_nics(self) -> list[Interface]:
        """Physical NICs not currently enslaved to a bond/bridge."""
        return [i for i in self.interfaces if i.kind == "physical" and i.master is None]

    def link_names(self) -> set[str]:
        return {i.name for i in self.interfaces}

    # -- docker -----------------------------------------------------------
    def docker_network(self, name: str) -> DockerNetwork | None:
        return next((n for n in self.docker_networks if n.name == name), None)

    def docker_network_for_bridge(self, ifname: str) -> DockerNetwork | None:
        """The docker network backed by the given host bridge ifname, if any."""
        return next((n for n in self.docker_networks if n.bridge == ifname), None)

    def containers_on(self, network: str) -> list[Container]:
        return [c for c in self.containers if network in c.networks]

    def docker_bridge_names(self) -> set[str]:
        return {n.bridge for n in self.docker_networks if n.bridge}

    def is_docker_owned(self, iface: Interface) -> bool:
        """True for a docker-managed link — a docker bridge, or a member of one.
        NetGrip shows these read-only: altering them (delete, add member, change
        the gateway address, move an address) breaks docker, so they're edited
        through docker / compose, not here."""
        return iface.docker_network is not None or iface.master in self.docker_bridge_names()

    def uplink(self) -> Interface | None:
        """The interface carrying the IPv4 default route — the host's edge, used
        to anchor the dashed published-port connectors. None if no default route
        (or its device isn't in the interface list)."""
        for iface in self.interfaces:
            if 4 in iface.gateways:
                return iface
        return None
