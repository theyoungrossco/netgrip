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
