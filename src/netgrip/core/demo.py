"""Canned interface data for the built-in demo host.

Lets people explore the canvas (and see the command plans netgrip would
run) without root and without touching a real network stack.
"""

from __future__ import annotations

from netgrip.core.backends import parse_backend
from netgrip.core.model import Address, Gateway, Interface

# Effective resolvers shown in demo mode (as if read from resolv.conf). Each
# also appears as a per-link resolver below, so the System DNS box can show its
# provenance: 192.168.1.1 from eth0, 9.9.9.9 from bond0.
DEMO_DNS = ["192.168.1.1", "9.9.9.9"]
DEMO_DNS_SEARCH = ["lan.example"]

# The demo host looks like a netplan-rendered server (bonds, bridges, a vlan-
# aware bridge): show it as netplan over systemd-networkd so the persistence
# indicator has something representative to display. Built through the real
# parser so demo and live hosts produce an identical Backend.
DEMO_BACKEND = parse_backend(
    "@@NM@@\ninactive\n@@NETWORKD@@\nactive\n@@NETPLAN@@\n01-netcfg.yaml\n"
)


def demo_interfaces() -> list[Interface]:
    return [
        Interface(
            name="lo", index=1, kind="loopback", state="up", mtu=65536,
            addresses=[
                Address("127.0.0.1", 8, 4, scope="host"),
                Address("::1", 128, 6, scope="host"),
            ],
        ),
        Interface(
            name="eth0", index=2, kind="physical", state="up",
            mac="52:54:00:a1:b2:c3", mtu=1500, alias="uplink",
            # Separate IPv4 and IPv6 defaults, each in its own protocol box.
            gateways={
                4: Gateway("192.168.1.1", dynamic=True),
                6: Gateway("2001:db8:1::1", dynamic=True),
            },
            dns=["192.168.1.1", "2001:db8:1::1"], dns_search=["lan.example"],
            dns_dynamic=True,
            addresses=[
                Address("192.168.1.10", 24, 4, dynamic=True),
                Address("192.168.1.11", 24, 4),  # a second v4: its own box in the group
                Address("2001:db8:1::10", 64, 6),
            ],
        ),
        Interface(
            name="eth1", index=3, kind="physical", state="up",
            mac="52:54:00:a1:b2:c4", mtu=1500, master="bond0",
        ),
        Interface(
            name="eth2", index=4, kind="physical", state="up",
            mac="52:54:00:a1:b2:c5", mtu=1500, master="bond0",
        ),
        Interface(
            name="bond0", index=5, kind="bond", state="up",
            mac="52:54:00:a1:b2:c4", mtu=1500, bond_mode="802.3ad",
            gateways={4: Gateway("10.0.0.1")},  # statically configured (not DHCP)
            dns=["9.9.9.9"],
            addresses=[Address("10.0.0.5", 24, 4)],
        ),
        Interface(
            name="bond0.40", index=6, kind="vlan", state="up",
            mac="52:54:00:a1:b2:c4", mtu=1500, vlan_id=40, vlan_parent="bond0",
            addresses=[Address("10.0.40.5", 24, 4)],
        ),
        Interface(
            name="wlan0", index=7, kind="physical", state="down",
            mac="52:54:00:a1:b2:c6", mtu=1500,
        ),
        # A veth pair, both ends in this namespace (as Proxmox's firewall
        # fwln/fwpr links appear): each names the other as its peer, drawn as a
        # single cable between them.
        Interface(
            name="veth-host", index=8, kind="veth", state="up",
            mac="52:54:00:a1:b2:c7", mtu=1500, peer="veth-ns",
        ),
        Interface(
            name="veth-ns", index=9, kind="veth", state="up",
            mac="52:54:00:a1:b2:c8", mtu=1500, peer="veth-host",
        ),
        # A vlan-aware bridge (as Proxmox sets up): a trunk uplink carrying two
        # tagged VLANs and a VM tap as an untagged access port on VLAN 20.
        Interface(
            name="vmbr0", index=10, kind="bridge", state="up",
            mac="52:54:00:a1:b2:c9", mtu=1500, bridge_vlan_aware=True,
        ),
        Interface(
            name="eth3", index=11, kind="physical", state="up",
            mac="52:54:00:a1:b2:ca", mtu=1500, master="vmbr0",
            vlan_tags=["20", "30"],
        ),
        Interface(
            name="tap200i0", index=12, kind="tun", state="up",
            mac="52:54:00:a1:b2:cb", mtu=1500, master="vmbr0", pvid=20,
        ),
    ]
