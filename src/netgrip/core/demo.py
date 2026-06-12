"""Canned interface data for the built-in demo host.

Lets people explore the canvas (and see the command plans netgrip would
run) without root and without touching a real network stack.
"""

from __future__ import annotations

from netgrip.core.model import Address, Interface


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
            mac="52:54:00:a1:b2:c3", mtu=1500,
            addresses=[
                Address("192.168.1.10", 24, 4, dynamic=True),
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
    ]
