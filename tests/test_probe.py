"""Parsing of `ip -details -json address show` output."""

import os
import shutil
import subprocess

from netgrip.core.model import Gateway, Interface
from netgrip.core.probe import (
    _LINKDNS,
    _LINKDOMAIN,
    DNS_COMMAND,
    DOCKER_NETWORK_COMMAND,
    _endpoint_ip,
    apply_docker,
    parse_addr_json,
    parse_bridge_vlan_json,
    parse_docker_containers,
    parse_docker_networks,
    parse_port_bindings,
    parse_resolv_conf,
    parse_resolvectl_links,
    parse_route_json,
    parse_stats_json,
    parse_wg_dump,
    parse_wireless,
    probe_dns,
    probe_docker,
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
    # A veth whose peer lives in another namespace (a container): the peer
    # ifindex is given with a link_netnsid, and here it deliberately collides
    # with eth0's ifindex (2) — the resolver must NOT mis-pair it to eth0.
    {
        "ifindex": 8, "ifname": "vethC", "link_index": 2, "link_netnsid": 0,
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "52:54:00:77:88:03",
        "linkinfo": {"info_kind": "veth"}, "addr_info": [],
    },
    # A WireGuard tunnel. Real WireGuard interfaces have link_type "none" and
    # NO "address" field at all — the probe must handle a missing MAC gracefully.
    {
        "ifindex": 9, "ifname": "wg0",
        "flags": ["POINTOPOINT", "NOARP", "UP", "LOWER_UP"], "mtu": 1420,
        "operstate": "UNKNOWN", "link_type": "none",
        "linkinfo": {"info_kind": "wireguard"},
        "addr_info": [
            {"family": "inet", "local": "10.200.0.1", "prefixlen": 24, "scope": "global"},
        ],
    },
]


def test_parses_all_interfaces():
    ifaces = parse_addr_json(FIXTURE)
    assert [i.name for i in ifaces] == [
        "lo", "eth0", "eth0.100", "bond0", "eth1", "vethA", "vethB", "vethC", "wg0",
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
    # The container case: the far end is in another netns (link_netnsid set), so
    # its ifindex must not resolve — even though it collides with eth0's (2).
    ifaces = {i.name: i for i in parse_addr_json(FIXTURE)}
    vethc = ifaces["vethC"]
    assert vethc.kind == "veth"
    assert vethc.peer is None
    assert ifaces["eth0"].index == 2  # the index vethC's peer collides with


def test_bond_and_member():
    ifaces = parse_addr_json(FIXTURE)
    bond, member = ifaces[3], ifaces[4]
    assert bond.kind == "bond"
    assert bond.bond_mode == "802.3ad"
    assert bond.is_group
    # A slave has linkinfo with only info_slave_kind: still a physical NIC.
    assert member.kind == "physical"
    assert member.master == "bond0"


def test_bridge_vlan_aware_flag():
    payload = [{
        "ifindex": 10, "ifname": "vmbr0",
        "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"], "mtu": 1500,
        "operstate": "UP", "link_type": "ether", "address": "aa:bb:cc:dd:ee:ff",
        "linkinfo": {"info_kind": "bridge", "info_data": {"vlan_filtering": 1}},
        "addr_info": [],
    }]
    br = parse_addr_json(payload)[0]
    assert br.kind == "bridge"
    assert br.bridge_vlan_aware is True


def test_parse_bridge_vlan_json_splits_tagged_and_native():
    payload = [
        {"ifname": "eth3", "vlans": [
            {"vlan": 1, "flags": ["PVID", "Egress Untagged"]},
            {"vlan": 20},
            {"vlan": 100, "vlanEnd": 200},
        ]},
        {"ifname": "tap200i0", "vlans": [
            {"vlan": 20, "flags": ["PVID", "Egress Untagged"]},
        ]},
    ]
    table = parse_bridge_vlan_json(payload)
    # Trunk uplink: VLAN 1 native/untagged, 20 and the 100-200 range tagged.
    assert table["eth3"] == (1, ["20", "100-200"])
    # Access port: untagged on VLAN 20, nothing tagged.
    assert table["tap200i0"] == (20, [])


def test_parse_wireless_lists_phy80211_devices():
    # WIRELESS_COMMAND prints one netdev name per line for each that carries an
    # 802.11 phy; blank lines (nothing matched the glob) yield an empty set.
    assert parse_wireless("wlan0\nwlp3s0\n") == {"wlan0", "wlp3s0"}
    assert parse_wireless("  wlan0  \n\n") == {"wlan0"}
    assert parse_wireless("") == set()


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


# Trimmed but structurally faithful `docker network inspect` output: the default
# bridge network (host bridge from the option) and a user network (no option, so
# the bridge defaults to br-<id12>).
DOCKER_NETWORKS = [
    {
        "Name": "bridge",
        "Id": "0123456789abcdef0000",
        "Driver": "bridge",
        "Options": {"com.docker.network.bridge.name": "docker0"},
        "IPAM": {"Config": [{"Subnet": "172.17.0.0/16", "Gateway": "172.17.0.1"}]},
    },
    {
        "Name": "web",
        "Id": "abc123def4567890aaaa",
        "Driver": "bridge",
        "Options": {},
        "IPAM": {"Config": [{"Subnet": "172.18.0.0/16", "Gateway": "172.18.0.1"}]},
    },
    {"Name": "hostnet", "Id": "ff00", "Driver": "host", "Options": {}, "IPAM": {}},
]

# Trimmed `docker inspect <containers>`: a composed web container publishing two
# ports (v4+v6 on one of them), an un-composed container with no ports, and a
# host-network container (NetworkMode=host, no docker-assigned IPs).
DOCKER_CONTAINERS = [
    {
        "Id": "b2c3d4e5f6a7b8c9d0e1",
        "Name": "/shop-web-1",
        "Config": {
            "Image": "nginx:1.27",
            "Labels": {
                "com.docker.compose.project": "shop",
                "com.docker.compose.service": "web",
            },
        },
        "HostConfig": {"NetworkMode": "web"},
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Ports": {
                "80/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "8080"},
                    {"HostIp": "::", "HostPort": "8080"},
                ],
                "443/tcp": [{"HostIp": "192.168.1.10", "HostPort": "8443"}],
                "9000/tcp": None,  # exposed but not published
            },
            "Networks": {"web": {"IPAddress": "172.18.0.2", "NetworkID": "abc"}},
        },
    },
    {
        "Id": "c3d4e5f6a7b8c9d0e1f2",
        "Name": "/registry",
        "Config": {"Image": "registry:2", "Labels": {}},
        "HostConfig": {"NetworkMode": "bridge"},
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Ports": {},
            "Networks": {"bridge": {"IPAddress": "172.17.0.2"}},
        },
    },
    {
        "Id": "e5f6a7b8c9d0e1f2a3b4",
        "Name": "/plex",
        "Config": {
            "Image": "lscr.io/linuxserver/plex:latest",
            "Labels": {
                "com.docker.compose.project": "plex",
                "com.docker.compose.service": "plex",
            },
        },
        "HostConfig": {"NetworkMode": "host"},
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Ports": {"32400/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32400"}]},
            "Networks": {"host": {"IPAddress": ""}},  # host-net: empty IP
        },
    },
]


def test_parse_docker_networks_bridge_names_and_subnets():
    networks = parse_docker_networks(DOCKER_NETWORKS)
    by_name = {n.name: n for n in networks}
    # Default bridge takes its host bridge from the option.
    assert by_name["bridge"].bridge == "docker0"
    assert by_name["bridge"].subnets == ["172.17.0.0/16"]
    assert by_name["bridge"].gateway == "172.17.0.1"
    # A user network with no option defaults to br-<id12>.
    assert by_name["web"].bridge == "br-abc123def456"
    # A non-bridge driver carries no host bridge.
    assert by_name["hostnet"].driver == "host"
    assert by_name["hostnet"].bridge is None


def test_parse_docker_containers_labels_ip_and_ports():
    containers = parse_docker_containers(DOCKER_CONTAINERS)
    web = containers[0]
    assert web.name == "shop-web-1"  # leading slash stripped
    assert web.id == "b2c3d4e5f6a7"  # truncated to 12
    assert web.image == "nginx:1.27"
    assert web.composed and web.compose_project == "shop"
    assert web.label() == "shop/web"
    assert web.networks == {"web": "172.18.0.2"}
    assert web.network_mode == "web"
    # v4+v6 on the same publish collapse to one all-addresses mapping; the
    # pinned :443 keeps its host IP; the unpublished 9000 is dropped.
    labels = [p.label() for p in web.ports]
    assert labels == [":8080→80/tcp", "192.168.1.10:8443→443/tcp"]

    registry = containers[1]
    assert not registry.composed
    assert registry.label() == "registry"
    assert registry.ports == []
    assert registry.network_mode == "bridge"


def test_parse_docker_containers_host_network_mode():
    containers = parse_docker_containers(DOCKER_CONTAINERS)
    plex = containers[2]
    assert plex.name == "plex"
    assert plex.network_mode == "host"
    # Host-network containers have no docker-assigned IPs; the empty IPAddress
    # is filtered out so networks stays empty.
    assert plex.networks == {}
    # Published ports are still captured.
    assert len(plex.ports) == 1
    assert plex.ports[0].host_port == 32400


def test_interface_is_vm_tap():
    from netgrip.core.model import Interface
    tap = Interface(name="vnet0", kind="tun", master="br0")
    assert tap.is_vm_tap
    # A tun with no master (e.g. a VPN tunnel) is not a VM tap.
    assert not Interface(name="tun0", kind="tun").is_vm_tap
    # A physical NIC enslaved to a bridge is not a VM tap.
    assert not Interface(name="eth0", kind="physical", master="br0").is_vm_tap


def test_parse_port_bindings_dedupe_and_null():
    ports = {
        "53/udp": [{"HostIp": "0.0.0.0", "HostPort": "53"}],
        "80/tcp": None,
        "garbage": [{"HostIp": "0.0.0.0", "HostPort": "1"}],
    }
    mappings = parse_port_bindings(ports)
    assert len(mappings) == 1
    assert mappings[0].protocol == "udp"
    assert mappings[0].container_port == 53
    assert mappings[0].all_host_ips


def test_apply_docker_tags_bridge_interface():
    interfaces = [Interface(name="docker0", kind="bridge"),
                  Interface(name="eth0", kind="physical")]
    apply_docker(interfaces, parse_docker_networks(DOCKER_NETWORKS))
    assert interfaces[0].docker_network == "bridge"
    assert interfaces[1].docker_network is None


class _DockerRunner:
    """Returns the networks JSON for the network read, containers for the other."""

    def run(self, argv):
        import json
        if argv == DOCKER_NETWORK_COMMAND:
            return json.dumps(DOCKER_NETWORKS)
        return json.dumps(DOCKER_CONTAINERS)


class _NoDockerRunner:
    def run(self, argv):
        raise RuntimeError("docker: command not found")


def test_probe_docker_reads_both():
    networks, containers = probe_docker(_DockerRunner())
    assert {n.name for n in networks} == {"bridge", "web", "hostnet"}
    assert {c.name for c in containers} == {"shop-web-1", "registry", "plex"}


def test_probe_docker_best_effort_when_absent():
    # No docker (or no daemon access): yields nothing, never raises.
    assert probe_docker(_NoDockerRunner()) == ([], [])


def test_wireguard_no_mac():
    # WireGuard interfaces have no "address" field in iproute2 JSON output
    # (link_type is "none"). Parse must not crash and must produce empty mac.
    ifaces = {i.name: i for i in parse_addr_json(FIXTURE)}
    wg = ifaces["wg0"]
    assert wg.kind == "wireguard"
    assert wg.mac == ""
    assert wg.mtu == 1420
    assert wg.is_up
    assert [a.cidr for a in wg.addresses_for(4)] == ["10.200.0.1/24"]


# Trimmed `ip -s -j link show` output: one interface with stats64 (the common
# 64-bit block) and one with only the older 32-bit stats block as a fallback.
STATS_FIXTURE = [
    {
        "ifname": "eth0",
        "stats64": {
            "rx": {"bytes": 5_400_907_383, "packets": 3_928_098, "errors": 0},
            "tx": {"bytes": 494_957_079, "packets": 1_251_283, "errors": 0},
        },
    },
    {
        "ifname": "lo",
        "stats": {
            "rx": {"bytes": 1_000, "packets": 10},
            "tx": {"bytes": 1_000, "packets": 10},
        },
    },
    {
        "ifname": "wg0",
        # No stats block at all: must yield zeros, not crash.
    },
]


def test_parse_stats_json_reads_64bit_counters():
    result = {name: (rx, tx) for name, rx, tx in parse_stats_json(STATS_FIXTURE)}
    assert result["eth0"] == (5_400_907_383, 494_957_079)


def test_parse_stats_json_falls_back_to_32bit():
    result = {name: (rx, tx) for name, rx, tx in parse_stats_json(STATS_FIXTURE)}
    assert result["lo"] == (1_000, 1_000)


def test_parse_stats_json_missing_block_yields_zeros():
    result = {name: (rx, tx) for name, rx, tx in parse_stats_json(STATS_FIXTURE)}
    assert result["wg0"] == (0, 0)


# `wg show wg0 dump` fixture: interface row, then two peers — one with a
# known endpoint and transfer counts, one roaming (no endpoint, never connected).
WG_DUMP = "\t".join([
    "oI2lGBFJGUi67bJqnXB9DfKHJCiuSXYpCvv2XAp8Vw=",  # private-key
    "PubKeyForInterface=",                              # public-key
    "51820",                                            # listen-port
    "off",                                              # fwmark
]) + "\n" + "\t".join([
    "XNnEBFJGUi67bJqnXB9DfKHJCiuSXYpCvv2XAp8Vw=",  # public-key
    "vCHJEoW7gvJdKrPq3V3u8yTjHZBFKLIe1dX2t3S4=",   # preshared-key
    "198.51.100.1:51820",                               # endpoint
    "10.200.0.2/32,192.168.100.0/24",                  # allowed-ips
    "1700000000",                                       # latest-handshake
    "524288",                                           # rx-bytes
    "393216",                                           # tx-bytes
    "off",                                              # keepalive
]) + "\n" + "\t".join([
    "yMnEBFJGUi67bJqnXB9DfKHJCiuSXYpCvv2XAp8Vw=",  # public-key (roaming)
    "(none)",                                           # no preshared-key
    "(none)",                                           # no endpoint
    "10.200.0.3/32",                                    # allowed-ips
    "0",                                                # never connected
    "0", "0",                                           # no transfer
    "25",                                               # keepalive
])


def test_parse_wg_dump_returns_peers_skips_interface_row():
    peers = parse_wg_dump(WG_DUMP)
    assert len(peers) == 2


def test_parse_wg_dump_first_peer_fields():
    peers = parse_wg_dump(WG_DUMP)
    p = peers[0]
    assert p.public_key == "XNnEBFJGUi67bJqnXB9DfKHJCiuSXYpCvv2XAp8Vw="
    assert p.endpoint == "198.51.100.1:51820"
    assert p.allowed_ips == ["10.200.0.2/32", "192.168.100.0/24"]
    assert p.latest_handshake == 1_700_000_000
    assert p.rx_bytes == 524_288
    assert p.tx_bytes == 393_216
    assert p.egress_dev is None  # not set by parse, only by probe_wg


def test_parse_wg_dump_roaming_peer_has_empty_endpoint_and_no_allowed_ips_from_none():
    peers = parse_wg_dump(WG_DUMP)
    p = peers[1]
    assert p.public_key == "yMnEBFJGUi67bJqnXB9DfKHJCiuSXYpCvv2XAp8Vw="
    assert p.endpoint == ""  # "(none)" -> ""
    assert p.allowed_ips == ["10.200.0.3/32"]
    assert p.latest_handshake == 0
    assert p.rx_bytes == 0
    assert p.tx_bytes == 0


def test_parse_wg_dump_empty_yields_empty():
    assert parse_wg_dump("") == []
    # Only an interface row, no peers.
    assert parse_wg_dump("privkey\tpubkey\t51820\toff") == []


def test_parse_wg_dump_malformed_line_skipped():
    # A line with fewer than 7 fields is silently dropped.
    dump = "privkey\tpubkey\t51820\toff\n" "pubkey\tpsk\t(none)"  # only 3 fields
    assert parse_wg_dump(dump) == []


def test_endpoint_ip_extracts_ipv4():
    assert _endpoint_ip("198.51.100.1:51820") == "198.51.100.1"


def test_endpoint_ip_extracts_ipv6():
    assert _endpoint_ip("[2001:db8::1]:51820") == "2001:db8::1"


def test_endpoint_ip_empty_returns_none():
    assert _endpoint_ip("") is None
    assert _endpoint_ip("(none)") is None
