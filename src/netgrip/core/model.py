"""Data model describing the network state of one host.

These classes are plain data carriers. They are produced by
:mod:`netgrip.core.probe` and consumed by the UI; they never talk to the
system themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Interface kinds that an IP configuration or a VLAN can be attached to.
ATTACHABLE_KINDS = {"physical", "bond", "bridge", "team", "vlan", "loopback"}

# Interface kinds rendered as a "group" (several NICs joined together).
GROUP_KINDS = {"bond", "bridge", "team"}


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
    gateway: str = ""  # default route reached via this link (from `ip route`)
    gateway_dynamic: bool = False  # the default route was installed by DHCP / RA
    addresses: list[Address] = field(default_factory=list)

    @property
    def is_up(self) -> bool:
        return self.state == "up"

    @property
    def is_group(self) -> bool:
        return self.kind in GROUP_KINDS

    def addresses_for(self, family: int) -> list[Address]:
        return [a for a in self.addresses if a.family == family]


@dataclass
class HostState:
    """Snapshot of all interfaces on one host."""

    label: str
    interfaces: list[Interface] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)  # effective nameservers (resolv.conf)
    dns_search: list[str] = field(default_factory=list)  # search domains
    can_edit_dns: bool = False  # systemd-resolved (resolvectl) present for per-link DNS

    def get(self, name: str) -> Interface | None:
        return next((i for i in self.interfaces if i.name == name), None)

    def members_of(self, group_name: str) -> list[Interface]:
        return [i for i in self.interfaces if i.master == group_name]

    def free_nics(self) -> list[Interface]:
        """Physical NICs not currently enslaved to a bond/bridge."""
        return [i for i in self.interfaces if i.kind == "physical" and i.master is None]

    def link_names(self) -> set[str]:
        return {i.name for i in self.interfaces}
