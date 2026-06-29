"""Canned interface data for the built-in demo host.

Lets people explore the canvas (and see the command plans netgrip would
run) without root and without touching a real network stack.
"""

from __future__ import annotations

from netgrip.core.backends import parse_backend
from netgrip.core.model import (
    Address,
    Container,
    DockerNetwork,
    FirewallState,
    Gateway,
    Interface,
    NftChain,
    NftRule,
    NftTable,
    PortMapping,
)

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
            rx_bytes=5_400_907_383, tx_bytes=494_957_079,
        ),
        Interface(
            name="eth1", index=3, kind="physical", state="up",
            mac="52:54:00:a1:b2:c4", mtu=1500, master="bond0",
            rx_bytes=12_345_678, tx_bytes=8_901_234,
        ),
        Interface(
            name="eth2", index=4, kind="physical", state="up",
            mac="52:54:00:a1:b2:c5", mtu=1500, master="bond0",
            rx_bytes=11_222_333, tx_bytes=7_654_321,
        ),
        Interface(
            name="bond0", index=5, kind="bond", state="up",
            mac="52:54:00:a1:b2:c4", mtu=1500, bond_mode="802.3ad",
            gateways={4: Gateway("10.0.0.1")},  # statically configured (not DHCP)
            dns=["9.9.9.9"],
            addresses=[Address("10.0.0.5", 24, 4)],
            rx_bytes=23_567_011, tx_bytes=16_555_555,
        ),
        Interface(
            name="bond0.40", index=6, kind="vlan", state="up",
            mac="52:54:00:a1:b2:c4", mtu=1500, vlan_id=40, vlan_parent="bond0",
            addresses=[Address("10.0.40.5", 24, 4)],
        ),
        Interface(
            name="wlan0", index=7, kind="physical", state="down",
            mac="52:54:00:a1:b2:c6", mtu=1500, wireless=True,
        ),
        # A WireGuard VPN tunnel. Real WireGuard interfaces have no MAC address
        # (link_type "none" in iproute2) and a reduced MTU to leave room for the
        # WireGuard header. The tunnel IP is a dedicated VPN subnet.
        Interface(
            name="wg0", index=18, kind="wireguard", state="up",
            mtu=1420,
            addresses=[Address("10.200.0.1", 24, 4)],
            rx_bytes=1_048_576, tx_bytes=786_432,
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
        # A Docker host: the default bridge (docker0) with one standalone
        # container, and a user network (br-…) carrying a two-service compose
        # project. Each container's host-side veth is a bridge member; the
        # container itself is drawn from the docker read (demo_docker) below.
        Interface(
            name="docker0", index=13, kind="bridge", state="up",
            mac="02:42:9b:11:22:01", mtu=1500,
            addresses=[Address("172.17.0.1", 16, 4)],  # docker's bridge gateway
        ),
        Interface(
            name="veth1a2b3c", index=14, kind="veth", state="up",
            mac="02:42:9b:11:22:02", mtu=1500, master="docker0",
        ),
        Interface(
            name="br-abc123def456", index=15, kind="bridge", state="up",
            mac="02:42:9b:11:22:10", mtu=1500,
            addresses=[Address("172.18.0.1", 16, 4)],  # the "web" network gateway
        ),
        Interface(
            name="veth4d5e6f", index=16, kind="veth", state="up",
            mac="02:42:9b:11:22:11", mtu=1500, master="br-abc123def456",
        ),
        Interface(
            name="veth7a8b9c", index=17, kind="veth", state="up",
            mac="02:42:9b:11:22:12", mtu=1500, master="br-abc123def456",
        ),
    ]


def demo_firewall() -> FirewallState:
    """Representative nftables ruleset for the built-in demo host.

    Models a typical server setup: inet filter table with INPUT/FORWARD/OUTPUT
    base chains, and a nat table for masquerade on the uplink (eth0).  Rules
    reference eth0 and bond0 so the per-interface firewall panel has something
    to show on those boxes."""
    filter_input = NftChain(
        name="INPUT", family="inet", table="filter", handle=1,
        chain_type="filter", hook="input", prio=0, policy="drop",
        rules=[
            NftRule(
                handle=1, family="inet", table="filter", chain="INPUT",
                ifaces=["lo"],
                expr_summary="iifname == lo accept",
            ),
            NftRule(
                handle=2, family="inet", table="filter", chain="INPUT",
                ifaces=[],
                expr_summary="ct state {established, related} accept",
            ),
            NftRule(
                handle=3, family="inet", table="filter", chain="INPUT",
                ifaces=["eth0"],
                comment="ssh from uplink",
                expr_summary="iifname == eth0 tcp.dport == 22 accept",
            ),
            NftRule(
                handle=4, family="inet", table="filter", chain="INPUT",
                ifaces=["bond0"],
                expr_summary="iifname == bond0 accept",
            ),
        ],
    )
    filter_forward = NftChain(
        name="FORWARD", family="inet", table="filter", handle=2,
        chain_type="filter", hook="forward", prio=0, policy="drop",
        rules=[
            NftRule(
                handle=5, family="inet", table="filter", chain="FORWARD",
                ifaces=["eth0"],
                expr_summary="oifname == eth0 ct state {established, related} accept",
            ),
            NftRule(
                handle=6, family="inet", table="filter", chain="FORWARD",
                ifaces=["bond0", "eth0"],
                expr_summary="iifname == bond0 oifname == eth0 accept",
            ),
        ],
    )
    filter_output = NftChain(
        name="OUTPUT", family="inet", table="filter", handle=3,
        chain_type="filter", hook="output", prio=0, policy="accept",
    )
    filter_table = NftTable(
        name="filter", family="inet", handle=1,
        chains=[filter_input, filter_forward, filter_output],
    )

    nat_postrouting = NftChain(
        name="POSTROUTING", family="ip", table="nat", handle=1,
        chain_type="nat", hook="postrouting", prio=100, policy="accept",
        rules=[
            NftRule(
                handle=7, family="ip", table="nat", chain="POSTROUTING",
                ifaces=["eth0"],
                expr_summary="oifname == eth0 masquerade",
            ),
        ],
    )
    nat_table = NftTable(
        name="nat", family="ip", handle=2,
        chains=[nat_postrouting],
    )

    return FirewallState(tables=[filter_table, nat_table], available=True)


# Docker networks and the running containers on them, as `docker network
# inspect` / `docker inspect` would report. `br-abc123def456` matches the
# bridge ifname derived from the "web" network id (br-<id[:12]>).
def demo_docker() -> tuple[list[DockerNetwork], list[Container]]:
    networks = [
        DockerNetwork(
            name="bridge", id="0123456789ab", driver="bridge", bridge="docker0",
            subnets=["172.17.0.0/16"], gateway="172.17.0.1",
        ),
        DockerNetwork(
            name="web", id="abc123def4567890", driver="bridge",
            bridge="br-abc123def456",
            subnets=["172.18.0.0/16"], gateway="172.18.0.1",
        ),
    ]
    containers = [
        Container(
            name="registry", id="a1b2c3d4e5f6", image="registry:2",
            networks={"bridge": "172.17.0.2"},
            ports=[PortMapping("0.0.0.0", 5000, 5000, "tcp")],
        ),
        Container(
            name="plex", id="d4e5f6a7b8c9", image="lscr.io/linuxserver/plex:latest",
            compose_project="plex", compose_service="plex",
            network_mode="host",
            ports=[PortMapping("0.0.0.0", 32400, 32400, "tcp")],
        ),
        Container(
            name="shop-web-1", id="b2c3d4e5f6a7", image="nginx:1.27",
            compose_project="shop", compose_service="web",
            networks={"web": "172.18.0.2"},
            ports=[
                PortMapping("0.0.0.0", 8080, 80, "tcp"),
                PortMapping("192.168.1.10", 8443, 443, "tcp"),
            ],
        ),
        Container(
            name="shop-db-1", id="c3d4e5f6a7b8", image="postgres:16",
            compose_project="shop", compose_service="db",
            networks={"web": "172.18.0.3"},
        ),
    ]
    return networks, containers
