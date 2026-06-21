"""Rendering link-layer properties into systemd .link files (persist_link.py)."""

from netgrip.core import persist_link
from netgrip.core.actions import write_file_preview
from netgrip.core.model import Interface
from netgrip.core.persist_link import ALIAS, MAC, MTU, NAME, LinkProps


def _iface(**kw) -> Interface:
    base = dict(name="enp3s0", kind="physical", state="up",
                mac="02:11:22:33:44:55", mtu=9000, alias="Uplink")
    base.update(kw)
    return Interface(**base)


# --- link_props: distilling running link-layer state ----------------------- #

def test_link_props_carries_changed_and_match_name():
    props = persist_link.link_props(_iface(), {MTU}, match_name="enp3s0")
    assert props.name == "enp3s0"
    assert props.match_name == "enp3s0"
    assert props.mtu == 9000
    assert props.changed == frozenset({MTU})


def test_link_props_defaults_match_to_current_name():
    props = persist_link.link_props(_iface(name="eth0"), {ALIAS})
    assert props.match_name == "eth0"


def test_renames_only_when_name_changed_and_differs():
    assert LinkProps("lan0", "enp3s0", changed=frozenset({NAME})).renames()
    # name in changed but match equals current: not a rename
    assert not LinkProps("eth0", "eth0", changed=frozenset({NAME})).renames()
    # name not in changed: not a rename even if names differ
    assert not LinkProps("lan0", "enp3s0", changed=frozenset({MTU})).renames()


# --- link_file: rendering -------------------------------------------------- #

def test_link_path_is_prefixed_drop_in():
    assert persist_link.link_path("lan0") == "/etc/systemd/network/10-netgrip-lan0.link"


def test_link_file_matches_by_original_name():
    out = persist_link.link_file(LinkProps("lan0", "enp3s0", changed=frozenset({NAME})))
    assert "[Match]" in out
    assert "OriginalName=enp3s0" in out
    assert "[Link]" in out
    assert "Name=lan0" in out


def test_link_file_renders_only_changed_properties():
    # alias changed alone: no Name/MACAddress/MTUBytes lines leak in
    out = persist_link.link_file(
        persist_link.link_props(_iface(), {ALIAS}, match_name="enp3s0")
    )
    assert "Alias=Uplink" in out
    assert "\nName=" not in out  # the [Link] Name= line (not OriginalName=)
    assert "MACAddress=" not in out
    assert "MTUBytes=" not in out


def test_link_file_empty_alias_clears_ifalias():
    out = persist_link.link_file(
        persist_link.link_props(_iface(alias=""), {ALIAS}, match_name="enp3s0")
    )
    assert "Alias=\n" in out  # empty value clears it


def test_link_file_mac_and_mtu():
    out = persist_link.link_file(
        persist_link.link_props(_iface(), {MAC, MTU}, match_name="enp3s0")
    )
    assert "MACAddress=02:11:22:33:44:55" in out
    assert "MTUBytes=9000" in out
    # no MACAddressPolicy line — a direct MACAddress= takes effect on its own
    assert "MACAddressPolicy" not in out


def test_link_file_combined_rename_and_props():
    out = persist_link.link_file(
        persist_link.link_props(_iface(name="lan0"), {NAME, MAC, MTU, ALIAS},
                                match_name="enp3s0")
    )
    assert "OriginalName=enp3s0" in out
    for line in ("Name=lan0", "Alias=Uplink", "MACAddress=02:11:22:33:44:55",
                 "MTUBytes=9000"):
        assert line in out


# --- plan_link_files: the write-through plan ------------------------------- #

def test_plan_link_files_writes_then_reloads():
    props = persist_link.link_props(_iface(), {MTU}, match_name="enp3s0")
    plan = persist_link.plan_link_files([props])
    # last step reloads udev's rules without disrupting the live link
    assert plan[-1] == ["udevadm", "control", "--reload"]
    # the write step is a plan_write_file shell heredoc the dialog can preview
    preview = write_file_preview(plan[0])
    assert preview is not None
    path, body = preview
    assert path == "/etc/systemd/network/10-netgrip-enp3s0.link"
    assert "MTUBytes=9000" in body


def test_plan_link_files_skips_unchanged_links():
    # a link recorded with no changed properties contributes nothing
    empty = LinkProps("eth0", "eth0")
    assert persist_link.plan_link_files([empty]) == []


def test_plan_link_files_empty_for_no_props():
    assert persist_link.plan_link_files([]) == []


def test_plan_link_files_one_file_per_changed_link():
    a = persist_link.link_props(_iface(name="eth0"), {MTU})
    b = persist_link.link_props(_iface(name="eth1"), {MAC})
    plan = persist_link.plan_link_files([a, b])
    paths = [write_file_preview(step)[0] for step in plan
             if write_file_preview(step) is not None]
    assert paths == ["/etc/systemd/network/10-netgrip-eth0.link",
                     "/etc/systemd/network/10-netgrip-eth1.link"]
    assert plan[-1] == ["udevadm", "control", "--reload"]
