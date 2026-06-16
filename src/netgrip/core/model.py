"""Data model describing the network state of one host.

These classes are plain data carriers. They are produced by
:mod:`netgrip.core.probe` and consumed by the UI; they never talk to the
system themselves.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

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
class Interface:
    name: str
    index: int = 0
    kind: str = "physical"  # physical | loopback | vlan | bond | bridge | veth | ...
    state: str = "down"  # up | down
    mac: str = ""
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
    # Per-family default route, keyed by family (4 / 6). See `Gateway`.
    gateways: dict[int, Gateway] = field(default_factory=dict)
    # Per-link DNS, as configured on this interface (systemd-resolved). These
    # are read where resolvectl is present; on plain resolv.conf hosts they are
    # empty and only the host-wide list on `HostState` is known.
    dns: list[str] = field(default_factory=list)
    dns_search: list[str] = field(default_factory=list)
    dns_dynamic: bool = False  # link DNS was handed out by DHCP / RA
    addresses: list[Address] = field(default_factory=list)

    @property
    def is_up(self) -> bool:
        return self.state == "up"

    @property
    def is_group(self) -> bool:
        return self.kind in GROUP_KINDS

    def addresses_for(self, family: int) -> list[Address]:
        return [a for a in self.addresses if a.family == family]

    def families(self) -> list[int]:
        """Address families present on this link, IPv4 first, deduplicated."""
        return [f for f in (4, 6) if any(a.family == f for a in self.addresses)]

    def gateway_for(self, family: int) -> Gateway | None:
        return self.gateways.get(family)

    def dns_for(self, family: int) -> list[str]:
        """The link's DNS servers that belong to ``family`` (by server IP)."""
        return [s for s in self.dns if ip_family(s) == family]


@dataclass
class HostState:
    """Snapshot of all interfaces on one host."""

    label: str
    interfaces: list[Interface] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)  # effective nameservers (resolv.conf)
    dns_search: list[str] = field(default_factory=list)  # search domains
    can_edit_dns: bool = False  # systemd-resolved (resolvectl) present for per-link DNS
    manual_dns: list[str] = field(default_factory=list)  # user-added extras (from store)

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
