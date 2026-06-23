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


def test_dhcp_dns_for_attributes_global_resolvers_to_a_dhcp_link():
    from netgrip.core.model import Address
    host_dns = ["192.168.1.1", "9.9.9.9", "2001:db8::1"]

    # A link with a DHCP lease (dynamic address) and no per-link DNS: the
    # host-wide IPv4 resolvers are inferred to come from its lease.
    dhcp_link = Interface(
        name="eth0",
        addresses=[Address("192.168.1.10", 24, 4, dynamic=True)],
        gateways={4: Gateway("192.168.1.1", dynamic=True)},
    )
    assert dhcp_link.uses_dhcp(4) is True
    assert dhcp_link.dhcp_dns_for(4, host_dns) == ["192.168.1.1", "9.9.9.9"]
    assert dhcp_link.dhcp_dns_for(6, host_dns) == []  # no IPv6 lease here

    # A statically configured link must not have global resolvers pinned to it.
    static_link = Interface(
        name="eth1",
        addresses=[Address("10.0.0.5", 24, 4)],
        gateways={4: Gateway("10.0.0.1")},
    )
    assert static_link.uses_dhcp(4) is False
    assert static_link.dhcp_dns_for(4, host_dns) == []

    # A link that already has per-link DNS uses that, not the global fallback.
    resolved_link = Interface(
        name="eth2",
        addresses=[Address("172.16.0.5", 24, 4, dynamic=True)],
        dns=["172.16.0.1"],
    )
    assert resolved_link.dhcp_dns_for(4, host_dns) == []


def test_configured_families_keeps_box_for_orphaned_gateway_or_dns():
    from netgrip.core.model import Address
    # An address makes a family configured (same as families()).
    eth0 = Interface(name="eth0", addresses=[Address("10.0.0.5", 24, 4)])
    assert eth0.configured_families() == [4]
    # Drop the address but keep a static gateway / DNS: the family still has
    # config to show, so its box must not vanish (it would orphan the gateway).
    eth0.addresses = []
    eth0.gateways = {4: Gateway("10.0.0.1")}
    eth0.dns = ["9.9.9.9"]
    assert eth0.configured_families() == [4]
    assert eth0.families() == []  # ...but it has no *address* family anymore
    # Truly empty: no box.
    assert Interface(name="bare").configured_families() == []


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


def test_host_state_docker_helpers():
    from netgrip.core.model import Container, DockerNetwork, HostState, PortMapping

    state = HostState(
        label="demo",
        interfaces=[Interface(name="docker0", kind="bridge"),
                    Interface(name="eth0", kind="physical",
                              gateways={4: Gateway("192.168.1.1")})],
        docker_networks=[DockerNetwork(name="bridge", bridge="docker0")],
        containers=[
            Container(name="a", networks={"bridge": "172.17.0.2"}),
            Container(name="b", networks={"web": "172.18.0.2"}),
        ],
    )
    assert state.docker_network("bridge").bridge == "docker0"
    assert state.docker_network_for_bridge("docker0").name == "bridge"
    assert state.docker_network_for_bridge("nope") is None
    assert [c.name for c in state.containers_on("bridge")] == ["a"]
    # The uplink is the link carrying the IPv4 default route.
    assert state.uplink().name == "eth0"

    # No default route anywhere -> no uplink.
    bare = HostState(label="x", interfaces=[Interface(name="eth0")])
    assert bare.uplink() is None

    # PortMapping label: all-addresses bind drops the host IP; a pinned one keeps it.
    assert PortMapping("0.0.0.0", 8080, 80, "tcp").label() == ":8080→80/tcp"
    assert PortMapping("10.0.0.1", 5, 6, "udp").label() == "10.0.0.1:5→6/udp"
