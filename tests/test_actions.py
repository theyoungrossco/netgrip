"""Command plans built by the actions module."""

import shlex

from netgrip.core import actions
from netgrip.core.model import Address, Interface


def test_install_ifupdown2_updates_then_installs_noninteractively():
    plan = actions.plan_install_ifupdown2()
    assert plan[0] == ["apt-get", "update"]
    # Installed non-interactively (no tty under the single sudo/ssh batch) via an
    # `env` prefix that survives as one argv, and with -y to skip the prompt.
    install = plan[-1]
    assert install[:2] == ["env", "DEBIAN_FRONTEND=noninteractive"]
    assert install[2:5] == ["apt-get", "install", "-y"]
    assert install[-1] == "ifupdown2"
    # Not a link change, so it must not be charged to any link's unsaved state.
    assert actions.affected_links(plan) == set()


def test_try_applies_forward_and_arms_detached_revert():
    forward = [["ip", "address", "add", "192.168.1.10/24", "dev", "eth1"]]
    revert = [["ip", "address", "del", "192.168.1.10/24", "dev", "eth1"]]
    plan = actions.plan_try(forward, revert, "abc123", timeout=70)
    # One command (so it confirms and runs as a single privileged batch).
    assert len(plan) == 1 and plan[0][:2] == ["sh", "-c"]
    script = plan[0][2]
    sentinel = f"{actions.TRY_STATE_DIR}/abc123"
    # Forward runs, the sentinel is created first, and the revert is armed behind
    # a sleep that only fires while the sentinel is present.
    assert "ip address add 192.168.1.10/24 dev eth1" in script
    assert f"touch {shlex.quote(sentinel)}" in script
    assert "sleep 70" in script
    assert f"[ -e {shlex.quote(sentinel)} ]" in script
    assert "ip address del 192.168.1.10/24 dev eth1" in script
    # The reverter must be detached (own session + stdio closed) so it survives
    # the SSH channel closing — that is what makes a dropped connection recover.
    assert "setsid" in script
    assert "</dev/null >/dev/null 2>&1 &" in script


def test_restore_uses_replace_so_revert_is_idempotent():
    # A DHCP client may re-add an address we removed during a Try; the revert
    # must not fail with "already assigned", so restoration uses replace.
    plan = actions.plan_restore_addresses("eth0", ["10.0.0.5/24"])
    assert plan == [["ip", "address", "replace", "10.0.0.5/24", "dev", "eth0"]]


def test_revert_join_is_tolerant_not_fail_fast():
    # Multi-step reverts chain with ';' so a benign failure (deleting an address
    # already gone) doesn't stop the remaining steps from restoring the rest.
    revert = [
        ["ip", "address", "del", "10.0.0.9/24", "dev", "eth1"],
        ["ip", "address", "replace", "10.0.0.5/24", "dev", "eth0"],
    ]
    script = actions.plan_revert_now("tok", revert)[0][2]
    assert "del 10.0.0.9/24 dev eth1; ip address replace 10.0.0.5/24" in script
    assert " && ip address replace" not in script  # not fail-fast


def test_keep_removes_sentinel_only():
    plan = actions.plan_keep("abc123")
    script = plan[0][2]
    assert script == f"rm -f {shlex.quote(actions.TRY_STATE_DIR + '/abc123')}"
    assert "sleep" not in script  # keeping never reverts anything


def test_revert_now_disarms_then_reverts():
    revert = [["ip", "address", "del", "192.168.1.10/24", "dev", "eth1"]]
    plan = actions.plan_revert_now("abc123", revert)
    script = plan[0][2]
    sentinel = shlex.quote(actions.TRY_STATE_DIR + "/abc123")
    # Removes the sentinel (so the armed host timer becomes a no-op) before
    # running the revert immediately.
    assert script.startswith(f"rm -f {sentinel};")
    assert script.endswith("ip address del 192.168.1.10/24 dev eth1")


def test_try_quotes_hostile_tokens_and_values():
    # Plans flow through one shell; nothing in them may break out of it.
    plan = actions.plan_try([["echo", "; rm -rf /"]], [["echo", "x"]], "t;evil")
    script = plan[0][2]
    # The dangerous argv is embedded shlex-quoted, not as raw shell.
    assert "echo '; rm -rf /'" in script
    # The token, too, is only ever used inside a quoted sentinel path.
    assert shlex.quote(actions.TRY_STATE_DIR + "/t;evil") in script


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
    # `replace` so it works whether or not a default route already exists; the
    # family flag keeps a v4 change from disturbing the v6 default.
    assert actions.plan_set_gateway("eth0", "192.168.1.1", 4) == [
        ["ip", "-4", "route", "replace", "default", "via", "192.168.1.1", "dev", "eth0"]
    ]
    assert actions.plan_set_gateway("eth0", "2001:db8::1", 6) == [
        ["ip", "-6", "route", "replace", "default", "via", "2001:db8::1", "dev", "eth0"]
    ]
    assert actions.plan_clear_gateway("eth0", 4) == [
        ["ip", "-4", "route", "del", "default", "dev", "eth0"]
    ]
    assert actions.plan_clear_gateway("eth0", 6) == [
        ["ip", "-6", "route", "del", "default", "dev", "eth0"]
    ]


def test_set_dns_plan_with_and_without_search():
    assert actions.plan_set_dns("eth0", ["1.1.1.1", "9.9.9.9"], []) == [
        ["resolvectl", "dns", "eth0", "1.1.1.1", "9.9.9.9"]
    ]
    assert actions.plan_set_dns("eth0", ["1.1.1.1"], ["lan.example"]) == [
        ["resolvectl", "dns", "eth0", "1.1.1.1"],
        ["resolvectl", "domain", "eth0", "lan.example"],
    ]


def test_write_file_makes_dir_and_uses_unexpanded_heredoc():
    plan = actions.plan_write_file("/etc/systemd/network/10-netgrip-eth0.network",
                                   "[Match]\nName=eth0\n")
    assert len(plan) == 1 and plan[0][:2] == ["sh", "-c"]
    script = plan[0][2]
    # The directory is created first, the file written via a *quoted* heredoc
    # (so the shell expands nothing in the body).
    assert "mkdir -p /etc/systemd/network &&" in script
    assert "cat > /etc/systemd/network/10-netgrip-eth0.network <<'NETGRIP_EOF'" in script
    assert "[Match]\nName=eth0\n" in script
    assert script.rstrip().endswith("NETGRIP_EOF")


def test_write_file_body_not_shell_expanded():
    # A '$' or backtick in the content must survive verbatim, not be expanded.
    plan = actions.plan_write_file("/etc/netplan/90-netgrip.yaml", "x: $HOME `id`\n")
    assert "x: $HOME `id`" in plan[0][2]


def test_write_file_preview_round_trips_path_and_body():
    body = "[Match]\nName=eth0\n\n[Network]\nAddress=10.0.0.5/24"
    plan = actions.plan_write_file("/etc/systemd/network/10-netgrip-eth0.network", body)
    path, recovered = actions.write_file_preview(plan[0])
    assert path == "/etc/systemd/network/10-netgrip-eth0.network"
    assert recovered == body  # the trailing newline plan_write_file adds is trimmed


def test_write_file_preview_ignores_other_commands():
    assert actions.write_file_preview(["ip", "addr", "add", "10.0.0.5/24", "dev", "eth0"]) is None
    assert actions.write_file_preview(["sh", "-c", "echo hi"]) is None


def test_next_bridge_name_skips_taken():
    assert actions.next_bridge_name(set()) == "br0"
    assert actions.next_bridge_name({"br0", "br1"}) == "br2"


def test_create_bridge_basic():
    plan = actions.plan_create_bridge("br0")
    assert plan[0] == ["ip", "link", "add", "br0", "type", "bridge"]
    assert plan[-1] == ["ip", "link", "set", "dev", "br0", "up"]
    # No vlan_filtering step without vlan_aware.
    assert not any("vlan_filtering" in " ".join(step) for step in plan)


def test_create_bridge_vlan_aware():
    plan = actions.plan_create_bridge("br0", vlan_aware=True)
    assert plan[0] == ["ip", "link", "add", "br0", "type", "bridge"]
    vlan_step = plan[1]
    assert vlan_step == ["ip", "link", "set", "dev", "br0", "type", "bridge",
                         "vlan_filtering", "1"]
    assert plan[-1] == ["ip", "link", "set", "dev", "br0", "up"]


def test_create_bridge_with_members_downs_before_enslaving():
    plan = actions.plan_create_bridge("br0", members=["eth1", "eth2"])
    assert plan[0] == ["ip", "link", "add", "br0", "type", "bridge"]
    eth1_down = plan.index(["ip", "link", "set", "dev", "eth1", "down"])
    eth1_master = plan.index(["ip", "link", "set", "dev", "eth1", "master", "br0"])
    eth1_up = plan.index(["ip", "link", "set", "dev", "eth1", "up"])
    assert eth1_down < eth1_master < eth1_up
    assert plan[-1] == ["ip", "link", "set", "dev", "br0", "up"]


def test_set_bridge_vlan_aware_enable_disable():
    on = actions.plan_set_bridge_vlan_aware("br0", True)
    assert on == [["ip", "link", "set", "dev", "br0", "type", "bridge",
                   "vlan_filtering", "1"]]
    off = actions.plan_set_bridge_vlan_aware("br0", False)
    assert off == [["ip", "link", "set", "dev", "br0", "type", "bridge",
                    "vlan_filtering", "0"]]


def test_bridge_vlan_add_tagged():
    plan = actions.plan_bridge_vlan_add("eth0", 100)
    assert plan == [["bridge", "vlan", "add", "dev", "eth0", "vid", "100"]]


def test_bridge_vlan_del():
    plan = actions.plan_bridge_vlan_del("eth0", 100)
    assert plan == [["bridge", "vlan", "del", "dev", "eth0", "vid", "100"]]


def test_bridge_pvid_set_no_old():
    plan = actions.plan_bridge_pvid_set("eth0", 20)
    assert plan == [["bridge", "vlan", "add", "dev", "eth0", "vid", "20",
                     "pvid", "untagged"]]


def test_bridge_pvid_set_removes_old_pvid_first():
    plan = actions.plan_bridge_pvid_set("eth0", 20, old_pvid=1)
    assert plan[0] == ["bridge", "vlan", "del", "dev", "eth0", "vid", "1"]
    assert plan[1] == ["bridge", "vlan", "add", "dev", "eth0", "vid", "20",
                       "pvid", "untagged"]


def test_bridge_pvid_set_same_vid_no_del():
    # When the requested PVID equals the old one, no del step is emitted.
    plan = actions.plan_bridge_pvid_set("eth0", 20, old_pvid=20)
    assert len(plan) == 1
    assert plan[0][0] != "bridge" or "del" not in plan[0]


def test_affected_links_collects_dev_name_and_positional_add():
    plan = (
        actions.plan_add_addresses("eth0", ["10.0.0.5/24"])
        + actions.plan_set_gateway("eth0", "10.0.0.1", 4)
        + actions.plan_create_bond("bond0", "active-backup", ["eth1", "eth2"])
        + actions.plan_create_vlan("eth0", 100)  # `ip link add link … name eth0.100`
    )
    links = actions.affected_links(plan)
    assert links == {"eth0", "eth1", "eth2", "bond0", "eth0.100"}
    # The gateway address is not mistaken for a link name.
    assert "10.0.0.1" not in links
