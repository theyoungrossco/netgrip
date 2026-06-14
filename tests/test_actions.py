"""Command plans built by the actions module."""

from netgrip.core import actions
from netgrip.core.model import Address, Interface


def test_move_addresses_deletes_then_adds():
    plan = actions.plan_move_addresses("eth0", "eth1", ["192.168.1.10/24"])
    assert plan == [
        ["ip", "address", "del", "192.168.1.10/24", "dev", "eth0"],
        ["ip", "address", "add", "192.168.1.10/24", "dev", "eth1"],
    ]


def test_create_vlan_defaults_name_and_brings_up():
    plan = actions.plan_create_vlan("eth0", 100)
    assert plan[0] == [
        "ip", "link", "add", "link", "eth0", "name", "eth0.100",
        "type", "vlan", "id", "100",
    ]
    assert plan[1] == ["ip", "link", "set", "dev", "eth0.100", "up"]


def test_create_bond_downs_members_before_enslaving():
    plan = actions.plan_create_bond("bond0", "active-backup", ["eth1", "eth2"])
    assert plan[0] == ["ip", "link", "add", "bond0", "type", "bond", "mode", "active-backup"]
    eth1_down = plan.index(["ip", "link", "set", "dev", "eth1", "down"])
    eth1_master = plan.index(["ip", "link", "set", "dev", "eth1", "master", "bond0"])
    assert eth1_down < eth1_master
    assert plan[-1] == ["ip", "link", "set", "dev", "bond0", "up"]


def test_move_vlan_recreates_with_addresses_and_conventional_rename():
    vlan = Interface(
        name="eth0.100", kind="vlan", state="up", vlan_id=100, vlan_parent="eth0",
        addresses=[Address("10.0.100.1", 24, 4)],
    )
    plan = actions.plan_move_vlan(vlan, "eth1")
    assert plan[0] == ["ip", "link", "del", "dev", "eth0.100"]
    assert plan[1] == [
        "ip", "link", "add", "link", "eth1", "name", "eth1.100",
        "type", "vlan", "id", "100",
    ]
    assert ["ip", "address", "add", "10.0.100.1/24", "dev", "eth1.100"] in plan
    assert plan[-1] == ["ip", "link", "set", "dev", "eth1.100", "up"]


def test_move_vlan_keeps_custom_name():
    vlan = Interface(name="dmz", kind="vlan", vlan_id=7, vlan_parent="eth0")
    plan = actions.plan_move_vlan(vlan, "eth1")
    assert ["ip", "link", "add", "link", "eth1", "name", "dmz",
            "type", "vlan", "id", "7"] in plan


def test_link_name_validation():
    assert actions.valid_link_name("eth0.100")
    assert actions.valid_link_name("bond0")
    assert not actions.valid_link_name("")
    assert not actions.valid_link_name("x" * 16)  # IFNAMSIZ
    assert not actions.valid_link_name("bad name")
    assert not actions.valid_link_name("semi;colon")


def test_next_bond_name_skips_taken():
    assert actions.next_bond_name(set()) == "bond0"
    assert actions.next_bond_name({"bond0", "bond1"}) == "bond2"


def test_mac_validation():
    assert actions.valid_mac("52:54:00:a1:b2:c3")
    assert actions.valid_mac("00:11:22:33:44:55")
    assert not actions.valid_mac("")
    assert not actions.valid_mac("52:54:00:a1:b2")  # too short
    assert not actions.valid_mac("zz:54:00:a1:b2:c3")  # non-hex
    assert not actions.valid_mac("52-54-00-a1-b2-c3")  # wrong separator
    # Multicast (low bit of first octet set) is not a valid device address.
    assert not actions.valid_mac("01:00:5e:00:00:01")


def test_set_mac_mtu_alias_plans():
    assert actions.plan_set_mac("eth0", "52:54:00:0a:0b:0c") == [
        ["ip", "link", "set", "dev", "eth0", "address", "52:54:00:0a:0b:0c"]
    ]
    assert actions.plan_set_mtu("eth0", 9000) == [
        ["ip", "link", "set", "dev", "eth0", "mtu", "9000"]
    ]
    assert actions.plan_set_alias("eth0", "uplink") == [
        ["ip", "link", "set", "dev", "eth0", "alias", "uplink"]
    ]
    # Empty alias clears the kernel ifalias.
    assert actions.plan_set_alias("eth0", "") == [
        ["ip", "link", "set", "dev", "eth0", "alias", ""]
    ]


def test_rename_link_downs_then_renames_and_restores_state():
    up = actions.plan_rename_link("eth0", "wan0", was_up=True)
    assert up == [
        ["ip", "link", "set", "dev", "eth0", "down"],
        ["ip", "link", "set", "dev", "eth0", "name", "wan0"],
        ["ip", "link", "set", "dev", "wan0", "up"],
    ]
    # A link that was down stays down — no trailing "up".
    down = actions.plan_rename_link("eth0", "wan0", was_up=False)
    assert down[-1] == ["ip", "link", "set", "dev", "eth0", "name", "wan0"]


def test_ipaddr_validation():
    assert actions.valid_ipaddr("192.168.1.1")
    assert actions.valid_ipaddr("2001:db8::1")
    assert not actions.valid_ipaddr("192.168.1.1/24")  # bare address, no prefix
    assert not actions.valid_ipaddr("not-an-ip")
    assert not actions.valid_ipaddr("")


def test_gateway_plans():
    # `replace` so it works whether or not a default route already exists.
    assert actions.plan_set_gateway("eth0", "192.168.1.1") == [
        ["ip", "route", "replace", "default", "via", "192.168.1.1", "dev", "eth0"]
    ]
    assert actions.plan_clear_gateway("eth0") == [
        ["ip", "route", "del", "default", "dev", "eth0"]
    ]


def test_set_dns_plan_with_and_without_search():
    assert actions.plan_set_dns("eth0", ["1.1.1.1", "9.9.9.9"], []) == [
        ["resolvectl", "dns", "eth0", "1.1.1.1", "9.9.9.9"]
    ]
    assert actions.plan_set_dns("eth0", ["1.1.1.1"], ["lan.example"]) == [
        ["resolvectl", "dns", "eth0", "1.1.1.1"],
        ["resolvectl", "domain", "eth0", "lan.example"],
    ]
