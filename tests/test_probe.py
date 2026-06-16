"""Parsing of `ip -details -json address show` output."""

import os
import shutil
import subprocess

from netgrip.core.model import Gateway
from netgrip.core.probe import (
    _LINKDNS,
    _LINKDOMAIN,
    DNS_COMMAND,
    parse_addr_json,
    parse_resolv_conf,
    parse_resolvectl_links,
    parse_route_json,
    probe_dns,
)

# Trimmed but structurally faithful iproute2 JSON for: loopback, a physical
# NIC with DHCP v4 + global v6 + link-local v6, a VLAN, a bond and a member.
FIXTURE = [
    {
        "ifindex": 1, "ifname": "lo",
        "flags": ["LOOPBACK", "UP", "LOWER_UP"], "mtu": 65536,
        "operstate": "UNKNOWN", "link_type": "loopback",
        "address": "00:00:00:00:00:00",
        "addr_info": [
            {"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"},
            {"family": "inet6", "local": "::1", "prefixlen": 128, "scope": "host"},
        ],
    },
    {
        "ifindex": 2, "ifname": "eth0", "ifalias": "uplink",
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:11:22:33",
        "addr_info": [
            {
                "family": "inet", "local": "192.168.1.10", "prefixlen": 24,
                "scope": "global", "dynamic": True, "label": "eth0",
            },
            {"family": "inet6", "local": "2001:db8::10", "prefixlen": 64, "scope": "global"},
            {"family": "inet6", "local": "fe80::5054:ff:fe11:2233", "prefixlen": 64,
             "scope": "link"},
        ],
    },
    {
        "ifindex": 3, "ifname": "eth0.100", "link": "eth0",
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:11:22:33",
        "linkinfo": {
            "info_kind": "vlan",
            "info_data": {"protocol": "802.1Q", "id": 100, "flags": ["REORDER_HDR"]},
        },
        "addr_info": [
            {"family": "inet", "local": "10.0.100.1", "prefixlen": 24, "scope": "global"},
        ],
    },
    {
        "ifindex": 4, "ifname": "bond0",
        "flags": ["BROADCAST", "MULTICAST", "MASTER", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:44:55:66",
        "linkinfo": {"info_kind": "bond", "info_data": {"mode": "802.3ad", "miimon": 100}},
        "addr_info": [],
    },
    {
        "ifindex": 5, "ifname": "eth1", "master": "bond0",
        "flags": ["BROADCAST", "MULTICAST", "SLAVE", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:44:55:66",
        "linkinfo": {"info_slave_kind": "bond"},
        "addr_info": [],
    },
    # A veth pair, both ends in this namespace: vethA reports the peer by
    # ifindex, vethB by name -- both paths must resolve to each other.
    {
        "ifindex": 6, "ifname": "vethA", "link_index": 7,
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:77:88:01",
        "linkinfo": {"info_kind": "veth"}, "addr_info": [],
    },
    {
        "ifindex": 7, "ifname": "vethB", "link": "vethA",
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:77:88:02",
        "linkinfo": {"info_kind": "veth"}, "addr_info": [],
    },
    # A veth whose peer lives in another namespace (a container): only an
    # ifindex we can't resolve locally, so it is left unpaired.
    {
        "ifindex": 8, "ifname": "vethC", "link_index": 4242,
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:77:88:03",
        "linkinfo": {"info_kind": "veth"}, "addr_info": [],
    },
]


def test_parses_all_interfaces():
    ifaces = parse_addr_json(FIXTURE)
    assert [i.name for i in ifaces] == [
        "lo", "eth0", "eth0.100", "bond0", "eth1", "vethA", "vethB", "vethC",
    ]


def test_loopback():
    lo = parse_addr_json(FIXTURE)[0]
    assert lo.kind == "loopback"
    assert lo.state == "up"  # operstate UNKNOWN but flags contain UP
    assert [a.cidr for a in lo.addresses] == ["127.0.0.1/8", "::1/128"]


def test_physical_nic_addresses():
    eth0 = parse_addr_json(FIXTURE)[1]
    assert eth0.kind == "physical"
    assert eth0.is_up
    assert eth0.mac == "52:54:00:11:22:33"
    assert eth0.alias == "uplink"  # kernel ifalias
    # A link without an ifalias parses to an empty string, not None.
    assert parse_addr_json(FIXTURE)[3].alias == ""
    v4 = eth0.addresses_for(4)
    assert [a.cidr for a in v4] == ["192.168.1.10/24"]
    assert v4[0].dynamic is True
    # Link-local IPv6 is filtered; the global address stays.
    assert [a.cidr for a in eth0.addresses_for(6)] == ["2001:db8::10/64"]


def test_vlan():
    vlan = parse_addr_json(FIXTURE)[2]
    assert vlan.kind == "vlan"
    assert vlan.vlan_id == 100
    assert vlan.vlan_parent == "eth0"


def test_veth_peers_resolve_both_ends():
    ifaces = {i.name: i for i in parse_addr_json(FIXTURE)}
    assert ifaces["vethA"].kind == "veth"
    # Resolved by ifindex (vethA -> 7) and by name (vethB -> "vethA").
    assert ifaces["vethA"].peer == "vethB"
    assert ifaces["vethB"].peer == "vethA"


def test_veth_peer_in_other_namespace_is_unpaired():
    # The container case: the far end isn't a local interface, so no peer.
    vethc = {i.name: i for i in parse_addr_json(FIXTURE)}["vethC"]
    assert vethc.kind == "veth"
    assert vethc.peer is None


def test_bond_and_member():
    ifaces = parse_addr_json(FIXTURE)
    bond, member = ifaces[3], ifaces[4]
    assert bond.kind == "bond"
    assert bond.bond_mode == "802.3ad"
    assert bond.is_group
    # A slave has linkinfo with only info_slave_kind: still a physical NIC.
    assert member.kind == "physical"
    assert member.master == "bond0"


ROUTE_FIXTURE = [
    {"dst": "default", "gateway": "10.99.0.1", "dev": "eth0", "protocol": "dhcp"},
    {"dst": "default", "gateway": "10.0.0.1", "dev": "bond0", "protocol": "static"},
    {"dst": "10.99.0.0/24", "dev": "eth0", "protocol": "kernel"},  # not a default
    {"dst": "default", "dev": "tun0"},  # no gateway -> skipped
]


def test_parse_route_json_picks_default_gateways_and_dynamic_flag():
    gws = parse_route_json(ROUTE_FIXTURE)
    assert gws["eth0"] == Gateway("10.99.0.1", True)   # dhcp -> dynamic
    assert gws["bond0"] == Gateway("10.0.0.1", False)  # static -> not dynamic
    assert "tun0" not in gws  # default route without a gateway is ignored


def test_parse_resolvectl_links_maps_link_to_servers():
    # `resolvectl dns` output: a Global line, then one line per link.
    text = (
        "Global:\n"
        "Link 2 (eth0): 192.168.1.1 2001:db8:1::1\n"
        "Link 3 (wlan0):\n"
    )
    links = parse_resolvectl_links(text)
    assert links == {"eth0": ["192.168.1.1", "2001:db8:1::1"], "wlan0": []}


def test_parse_resolvectl_links_domains():
    text = "Link 2 (eth0): lan.example ~corp.example\n"
    # Routing-only ('~') markers are left for the caller to strip.
    assert parse_resolvectl_links(text) == {"eth0": ["lan.example", "~corp.example"]}


def test_parse_resolv_conf_servers_and_search():
    text = (
        "# Generated by something\n"
        "search lan.example corp.example\n"
        "nameserver 192.168.1.1\n"
        "nameserver 9.9.9.9\n"
        "nameserver 192.168.1.1\n"  # duplicate -> kept once
    )
    servers, search = parse_resolv_conf(text)
    assert servers == ["192.168.1.1", "9.9.9.9"]
    assert search == ["lan.example", "corp.example"]


def test_parse_resolv_conf_empty():
    assert parse_resolv_conf("") == ([], [])


class _FakeRunner:
    """Returns one canned string for any read; enough to exercise probe_dns."""

    def __init__(self, output: str):
        self._output = output

    def run(self, argv):
        return self._output


def test_probe_dns_without_resolvectl_keeps_resolv_conf():
    # A host without systemd-resolved: capability marker 'no', resolv.conf
    # servers, then empty resolvectl sections. The host-wide resolvers must
    # still come through (the bug discarded them when the read "failed").
    output = (
        "no\n"
        "search lan.example\n"
        "nameserver 10.0.0.1\n"
        "nameserver 2001:db8::1\n"
        f"{_LINKDNS}\n{_LINKDOMAIN}\n"
    )
    servers, search, can_edit, per_link = probe_dns(_FakeRunner(output))
    assert servers == ["10.0.0.1", "2001:db8::1"]
    assert search == ["lan.example"]
    assert can_edit is False
    assert per_link == {}


def test_dns_command_exits_zero_without_resolvectl(tmp_path):
    # Regression: on a host without resolvectl, the command is "not found"
    # (exit 127). The DNS read must still exit 0 so the resolv.conf it already
    # gathered survives. Run the real script with a PATH that holds only the
    # tools it needs, guaranteeing resolvectl is absent.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("sh", "cat"):
        src = shutil.which(tool)
        assert src, f"{tool} must exist to run this test"
        os.symlink(src, bindir / tool)
    proc = subprocess.run(
        DNS_COMMAND, capture_output=True, text=True, env={"PATH": str(bindir)}
    )
    assert proc.returncode == 0
    assert _LINKDNS in proc.stdout
    assert _LINKDOMAIN in proc.stdout
