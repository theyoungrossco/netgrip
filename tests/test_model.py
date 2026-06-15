"""Model helpers: per-family gateways/DNS and resolver provenance."""

from netgrip.core.model import Gateway, HostState, Interface, ip_family
from netgrip.core.probe import apply_link_dns


def test_ip_family():
    assert ip_family("192.168.1.1") == 4
    assert ip_family("2001:db8::1") == 6
    assert ip_family("not-an-ip") is None


def test_interface_families_and_per_family_helpers():
    eth0 = Interface(name="eth0")
    eth0.addresses_for(4)  # empty link: no families yet
    assert eth0.families() == []

    eth0 = Interface(
        name="eth0",
        gateways={4: Gateway("192.168.1.1", dynamic=True)},
        dns=["192.168.1.1", "2001:db8::1"],
    )
    from netgrip.core.model import Address
    eth0.addresses = [Address("192.168.1.10", 24, 4), Address("2001:db8::10", 64, 6)]

    assert eth0.families() == [4, 6]  # IPv4 first
    assert eth0.gateway_for(4) == Gateway("192.168.1.1", dynamic=True)
    assert eth0.gateway_for(6) is None
    # DNS servers bucket into the family their own IP belongs to.
    assert eth0.dns_for(4) == ["192.168.1.1"]
    assert eth0.dns_for(6) == ["2001:db8::1"]


def test_apply_link_dns_attaches_and_marks_dynamic():
    from netgrip.core.model import Address
    eth0 = Interface(
        name="eth0",
        addresses=[Address("192.168.1.10", 24, 4, dynamic=True)],
    )
    static = Interface(name="static0", addresses=[Address("10.0.0.5", 24, 4)])
    apply_link_dns(
        [eth0, static],
        {
            "eth0": (["192.168.1.1"], ["lan.example", "~corp.example"]),
            "static0": (["9.9.9.9"], []),
        },
    )
    assert eth0.dns == ["192.168.1.1"]
    assert eth0.dns_search == ["lan.example", "corp.example"]  # '~' stripped
    assert eth0.dns_dynamic is True  # link has a dynamic address
    assert static.dns_dynamic is False  # static address -> not dynamic


def test_resolver_origin_traces_back_to_link_or_manual():
    eth0 = Interface(name="eth0", dns=["192.168.1.1"])
    state = HostState(
        "host", [eth0],
        dns=["192.168.1.1", "9.9.9.9", "1.1.1.1"],
        manual_dns=["1.1.1.1"],
    )
    assert state.resolver_origin("192.168.1.1") == "eth0"
    assert state.resolver_origin("1.1.1.1") == "manual"
    assert state.resolver_origin("9.9.9.9") == "system"
