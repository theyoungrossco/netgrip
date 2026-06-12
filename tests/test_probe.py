"""Parsing of `ip -details -json address show` output."""

from netgrip.core.probe import parse_addr_json

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
        "ifindex": 2, "ifname": "eth0",
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
]


def test_parses_all_interfaces():
    ifaces = parse_addr_json(FIXTURE)
    assert [i.name for i in ifaces] == ["lo", "eth0", "eth0.100", "bond0", "eth1"]


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


def test_bond_and_member():
    ifaces = parse_addr_json(FIXTURE)
    bond, member = ifaces[3], ifaces[4]
    assert bond.kind == "bond"
    assert bond.bond_mode == "802.3ad"
    assert bond.is_group
    # A slave has linkinfo with only info_slave_kind: still a physical NIC.
    assert member.kind == "physical"
    assert member.master == "bond0"
