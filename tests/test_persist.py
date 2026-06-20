"""Rendering a host's running IP config into backend config (persist.py)."""

import pytest

from netgrip.core import persist
from netgrip.core.backends import parse_backend
from netgrip.core.model import Address, Gateway, Interface

# Representative backends, built through the real parser so they match live ones.
NETWORKD = parse_backend("@@NM@@\ninactive\n@@NETWORKD@@\nactive\n@@NETPLAN@@\n")
NETPLAN = parse_backend("@@NM@@\ninactive\n@@NETWORKD@@\nactive\n@@NETPLAN@@\n01.yaml\n")
NM = parse_backend("@@NM@@\nactive\n@@NETWORKD@@\ninactive\n@@NETPLAN@@\n")
IFUPDOWN = parse_backend(
    "@@NM@@\ninactive\n@@NETWORKD@@\ninactive\n@@NETPLAN@@\n@@IFUPDOWN@@\nhasfile\nifreload\n"
)
RUNTIME = parse_backend("@@NM@@\ninactive\n@@NETWORKD@@\ninactive\n@@NETPLAN@@\n")


def _static_eth0() -> Interface:
    return Interface(
        name="eth0", kind="physical", state="up",
        gateways={4: Gateway("10.0.0.1")},
        dns=["9.9.9.9"],
        addresses=[Address("10.0.0.5", 24, 4)],
    )


# --- link_config: distilling running state --------------------------------- #

def test_link_config_keeps_static_drops_dynamic():
    iface = Interface(
        name="eth0", kind="physical",
        # A DHCP lease + a static address coexisting: the lease becomes the
        # dhcp4 flag, the static address is persisted, the dynamic one is not.
        gateways={4: Gateway("192.168.1.1", dynamic=True)},
        dns=["192.168.1.1"], dns_dynamic=True,
        addresses=[
            Address("192.168.1.10", 24, 4, dynamic=True),
            Address("10.0.0.5", 24, 4),
        ],
    )
    cfg = persist.link_config(iface)
    assert cfg.dhcp4 is True
    assert cfg.addresses == ["10.0.0.5/24"]   # the lease is not pinned static
    assert cfg.gateway4 == ""                  # a dynamic default route is dropped
    assert cfg.dns == []                        # DHCP-handed DNS is not persisted


def test_link_config_static_gateway_and_dns_kept():
    cfg = persist.link_config(_static_eth0())
    assert cfg.dhcp4 is False and cfg.dhcp6 is False
    assert cfg.addresses == ["10.0.0.5/24"]
    assert cfg.gateway4 == "10.0.0.1"
    assert cfg.dns == ["9.9.9.9"]


def test_link_config_skips_non_global_addresses():
    iface = Interface(name="lo", kind="loopback",
                      addresses=[Address("127.0.0.1", 8, 4, scope="host")])
    assert persist.link_config(iface).addresses == []


def test_set_dhcp_drops_family_static_and_flags_lease():
    # Switching IPv4 to DHCP (M5) keeps IPv6 static untouched.
    cfg = persist.LinkConfig(
        name="eth0",
        addresses=["10.0.0.5/24", "2001:db8::5/64"],
        gateway4="10.0.0.1", gateway6="2001:db8::1",
        dns=["9.9.9.9", "2001:4860:4860::8888"],
    )
    cfg.set_dhcp(4)
    assert cfg.dhcp4 is True
    assert cfg.addresses == ["2001:db8::5/64"]   # IPv4 static dropped, IPv6 kept
    assert cfg.gateway4 == "" and cfg.gateway6 == "2001:db8::1"
    assert cfg.dns == ["2001:4860:4860::8888"]   # IPv4 DNS dropped
    # The renderers then emit DHCP from the flag.
    assert "iface eth0 inet dhcp" in persist.ifupdown_file([cfg])
    assert "DHCP=ipv4" in persist.networkd_file(cfg)


# --- systemd-networkd ------------------------------------------------------ #

def test_networkd_file_static():
    text = persist.networkd_file(persist.link_config(_static_eth0()))
    assert "[Match]\nName=eth0" in text
    assert "Address=10.0.0.5/24" in text
    assert "Gateway=10.0.0.1" in text
    assert "DNS=9.9.9.9" in text
    assert "DHCP=" not in text  # purely static — no DHCP line


def test_networkd_file_dhcp_both_families():
    cfg = persist.LinkConfig(name="eth0", dhcp4=True, dhcp6=True)
    assert "DHCP=yes" in persist.networkd_file(cfg)
    assert "Address=" not in persist.networkd_file(cfg)


def test_networkd_file_dhcp_single_family():
    assert "DHCP=ipv4" in persist.networkd_file(persist.LinkConfig("eth0", dhcp4=True))
    assert "DHCP=ipv6" in persist.networkd_file(persist.LinkConfig("eth0", dhcp6=True))


def test_networkd_plan_writes_then_reloads_and_reconfigures():
    plan = persist.persist_plan([persist.link_config(_static_eth0())], NETWORKD)
    # First step writes the file (a plan_write_file heredoc step).
    assert plan[0][:2] == ["sh", "-c"]
    assert "/etc/systemd/network/10-netgrip-eth0.network" in plan[0][2]
    assert ["networkctl", "reload"] in plan
    assert ["networkctl", "reconfigure", "eth0"] in plan
    # reconfigure comes after reload.
    assert plan.index(["networkctl", "reload"]) < plan.index(
        ["networkctl", "reconfigure", "eth0"])


# --- netplan --------------------------------------------------------------- #

def test_netplan_yaml_sections_by_kind():
    configs = [
        persist.link_config(_static_eth0()),
        persist.LinkConfig(name="bond0", kind="bond", addresses=["172.16.0.1/24"]),
    ]
    text = persist.netplan_yaml(configs)
    assert "network:\n  version: 2" in text
    assert "  ethernets:\n    eth0:" in text
    assert "  bonds:\n    bond0:" in text
    assert "        - 10.0.0.5/24" in text
    # Gateway becomes a default route, not the deprecated gateway4 key.
    assert "      routes:\n        - to: default\n          via: 10.0.0.1" in text
    assert "gateway4" not in text


def test_netplan_plan_chmods_and_applies():
    plan = persist.persist_plan([persist.link_config(_static_eth0())], NETPLAN)
    assert plan[0][:2] == ["sh", "-c"]
    assert "/etc/netplan/90-netgrip.yaml" in plan[0][2]
    assert ["chmod", "600", "/etc/netplan/90-netgrip.yaml"] in plan
    assert ["netplan", "apply"] in plan


# --- ifupdown -------------------------------------------------------------- #

def test_ifupdown_file_static_and_dhcp():
    configs = [
        persist.link_config(_static_eth0()),
        persist.LinkConfig(name="eth1", dhcp4=True),
    ]
    text = persist.ifupdown_file(configs)
    assert "auto eth0" in text
    assert "iface eth0 inet static" in text
    assert "    address 10.0.0.5/24" in text
    assert "    gateway 10.0.0.1" in text
    assert "    dns-nameservers 9.9.9.9" in text
    assert "iface eth1 inet dhcp" in text
    # It is a drop-in, meant to be sourced from /etc/network/interfaces.
    assert "interfaces.d" in text


def test_ifupdown_file_dual_stack():
    cfg = persist.LinkConfig(
        name="eth0", addresses=["10.0.0.5/24", "2001:db8::5/64"],
        gateway4="10.0.0.1", gateway6="2001:db8::1",
    )
    text = persist.ifupdown_file([cfg])
    assert "iface eth0 inet static" in text
    assert "iface eth0 inet6 static" in text
    assert "    address 2001:db8::5/64" in text
    assert "    gateway 2001:db8::1" in text


def test_ifupdown_plan_writes_dropin_and_reloads():
    plan = persist.persist_plan([persist.link_config(_static_eth0())], IFUPDOWN)
    assert plan[0][:2] == ["sh", "-c"]
    assert "/etc/network/interfaces.d/90-netgrip.cfg" in plan[0][2]
    assert ["ifreload", "-a"] in plan


# --- NetworkManager -------------------------------------------------------- #

def test_parse_nm_connections_splits_device_and_unescapes_name():
    text = "eth0:Wired connection 1\nwlan0:Home\\:Net\n--:bridge-slave\n\n"
    conns = persist.parse_nm_connections(text)
    assert conns == {"eth0": "Wired connection 1", "wlan0": "Home:Net"}
    assert "--" not in conns  # a connection with no device is skipped


def test_nmcli_commands_static_then_up():
    cfg = persist.link_config(_static_eth0())
    plan = persist.nmcli_commands(cfg, "Wired connection 1")
    assert plan[0] == [
        "nmcli", "con", "mod", "Wired connection 1",
        "ipv4.method", "manual", "ipv4.addresses", "10.0.0.5/24",
        "ipv4.gateway", "10.0.0.1", "ipv4.dns", "9.9.9.9",
    ]
    assert plan[-1] == ["nmcli", "con", "up", "Wired connection 1"]


def test_nmcli_commands_dhcp_clears_stale_static():
    # Switching to DHCP must blank ipv4.addresses/gateway/dns, or NetworkManager
    # keeps re-applying the old static address (it reappears on con up).
    plan = persist.nmcli_commands(persist.LinkConfig("eth0", dhcp4=True), "conn")
    assert plan[0] == [
        "nmcli", "con", "mod", "conn",
        "ipv4.method", "auto",
        "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "",
    ]


def test_nmcli_commands_leaves_unconfigured_family_alone():
    # An IPv4-only config emits nothing for IPv6 (don't disturb its profile).
    plan = persist.nmcli_commands(persist.LinkConfig("eth0", addresses=["10.0.0.5/24"]), "conn")
    assert not any("ipv6" in a for a in plan[0])


def test_nm_persist_plan_resolves_connection():
    cfg = persist.link_config(_static_eth0())
    plan = persist.persist_plan([cfg], NM, {"eth0": "Wired connection 1"})
    assert plan[0][:4] == ["nmcli", "con", "mod", "Wired connection 1"]


def test_nm_persist_plan_errors_without_connection():
    cfg = persist.link_config(_static_eth0())
    with pytest.raises(persist.PersistError):
        persist.persist_plan([cfg], NM, connections={})


# --- dispatch & error handling --------------------------------------------- #

def test_persist_plan_runtime_raises():
    with pytest.raises(persist.PersistError):
        persist.persist_plan([persist.link_config(_static_eth0())], RUNTIME)
