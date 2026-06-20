"""Render a host's running IP configuration into persistent backend config.

This is the *Save* half of the 0.2 persistence work. NetGrip's mutations are
runtime ``ip`` commands; *Save* takes the IP config a link actually holds right
now (addresses, gateway, DNS, DHCP-vs-static per family) and writes it through
whichever subsystem owns the host — detected by :mod:`netgrip.core.backends` —
so it survives a reboot.

Scope is deliberately IP config only, not device topology: Save does not create
bonds/bridges/VLANs persistently (that is a later step), it configures the
addressing of links that already exist. Everything here is Qt-free and a pure
string/argv builder so it is exhaustively unit-testable; the one side-effecting
step, writing a file, goes through :func:`netgrip.core.actions.plan_write_file`
like every other mutation — a plan the user confirms before it runs.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from netgrip.core.actions import plan_write_file
from netgrip.core.backends import IFUPDOWN, NETPLAN, NETWORKD, NETWORKMANAGER, Backend
from netgrip.core.model import Interface, ip_family


class PersistError(RuntimeError):
    """Raised when a Save cannot be expressed for this host — a runtime-only or
    unknown backend (nowhere to write), or a NetworkManager link with no
    resolvable connection profile."""


@dataclass
class LinkConfig:
    """The persistable IP configuration of one link, distilled from running
    state. Family is carried on each value (addresses/DNS keep their own) so a
    renderer can split IPv4 from IPv6; ``dhcp4``/``dhcp6`` say a family takes a
    lease rather than (or alongside) a static address."""

    name: str
    kind: str = "physical"
    dhcp4: bool = False
    dhcp6: bool = False
    addresses: list[str] = field(default_factory=list)  # static CIDRs, both families
    gateway4: str = ""
    gateway6: str = ""
    dns: list[str] = field(default_factory=list)

    def addresses_for(self, family: int) -> list[str]:
        return [a for a in self.addresses if _cidr_family(a) == family]

    def dns_for(self, family: int) -> list[str]:
        return [s for s in self.dns if ip_family(s) == family]

    def set_dhcp(self, family: int) -> None:
        """Switch ``family`` to DHCP for persistence (M5): flag it and drop that
        family's static address, gateway and DNS, since the lease now provides
        them. The renderers already emit DHCP from the ``dhcp4``/``dhcp6`` flags,
        so this is all a 'static → Dynamic' Save needs."""
        if family == 4:
            self.dhcp4, self.gateway4 = True, ""
        else:
            self.dhcp6, self.gateway6 = True, ""
        self.addresses = [a for a in self.addresses if _cidr_family(a) != family]
        self.dns = [s for s in self.dns if ip_family(s) != family]


def link_config(iface: Interface) -> LinkConfig:
    """Distil a running :class:`Interface` into its persistable IP config.

    Keeps only what should be *written* as persistent: static, global addresses
    (a DHCP/RA lease is recorded as the ``dhcp*`` flag instead, not pinned as a
    static address), static gateways (a dynamic default route comes from the
    lease, so it is dropped), and static DNS (DHCP-handed resolvers likewise)."""
    addresses = [a.cidr for a in iface.addresses if not a.dynamic and a.scope == "global"]
    gw4 = iface.gateway_for(4)
    gw6 = iface.gateway_for(6)
    return LinkConfig(
        name=iface.name,
        kind=iface.kind,
        dhcp4=any(a.dynamic for a in iface.addresses_for(4)),
        dhcp6=any(a.dynamic for a in iface.addresses_for(6)),
        addresses=addresses,
        gateway4=gw4.address if gw4 and not gw4.dynamic else "",
        gateway6=gw6.address if gw6 and not gw6.dynamic else "",
        dns=[] if iface.dns_dynamic else list(iface.dns),
    )


# ---------------------------------------------------------------------------- #
# systemd-networkd: one .network file per link
# ---------------------------------------------------------------------------- #
NETWORKD_DIR = "/etc/systemd/network"


def networkd_path(name: str) -> str:
    """The drop-in NetGrip owns for ``name``. The ``10-`` prefix orders it ahead
    of distro defaults; the ``netgrip-`` tag makes it obvious who wrote it."""
    return f"{NETWORKD_DIR}/10-netgrip-{name}.network"


def networkd_file(cfg: LinkConfig) -> str:
    """Render a systemd ``.network`` unit for one link.

    ``[Match] Name=`` binds it to the device; ``[Network]`` carries the
    addressing. Safe to drop onto any existing link type — it only matches by
    name and sets addressing, it does not (re)create the device."""
    lines = ["[Match]", f"Name={cfg.name}", "", "[Network]"]
    dhcp = _networkd_dhcp(cfg.dhcp4, cfg.dhcp6)
    if dhcp:
        lines.append(f"DHCP={dhcp}")
    lines += [f"Address={addr}" for addr in cfg.addresses]
    if cfg.gateway4:
        lines.append(f"Gateway={cfg.gateway4}")
    if cfg.gateway6:
        lines.append(f"Gateway={cfg.gateway6}")
    lines += [f"DNS={server}" for server in cfg.dns]
    return "\n".join(lines) + "\n"


def _networkd_dhcp(dhcp4: bool, dhcp6: bool) -> str:
    """networkd's ``DHCP=`` value; empty (line omitted) when neither family
    leases — its default is ``no``."""
    if dhcp4 and dhcp6:
        return "yes"
    if dhcp4:
        return "ipv4"
    if dhcp6:
        return "ipv6"
    return ""


def _networkd_plan(configs: list[LinkConfig]) -> list[list[str]]:
    plan: list[list[str]] = []
    for cfg in configs:
        plan += plan_write_file(networkd_path(cfg.name), networkd_file(cfg))
    # Reload the unit files, then re-apply each touched link so the new config
    # takes effect without a reboot.
    plan.append(["networkctl", "reload"])
    plan += [["networkctl", "reconfigure", cfg.name] for cfg in configs]
    return plan


# ---------------------------------------------------------------------------- #
# netplan: one combined YAML, re-applied
# ---------------------------------------------------------------------------- #
NETPLAN_PATH = "/etc/netplan/90-netgrip.yaml"

# netplan groups devices by type. We only persist addressing onto links that
# already exist, but each must still sit under the right section.
_NETPLAN_SECTIONS = {
    "physical": "ethernets",
    "veth": "ethernets",
    "loopback": "ethernets",
    "bond": "bonds",
    "team": "bonds",
    "bridge": "bridges",
    "vlan": "vlans",
}


def _netplan_section(kind: str) -> str:
    return _NETPLAN_SECTIONS.get(kind, "ethernets")


def netplan_yaml(configs: list[LinkConfig]) -> str:
    """Render all links into one netplan v2 document.

    Hand-built (rather than via PyYAML) to keep the output deterministic, the
    module dependency-free, and the indentation reviewable in the confirm
    dialog. Experimental — netplan re-renders the whole stack on apply, so the
    confirm-dialog review is the safety check."""
    buckets: dict[str, list[LinkConfig]] = {}
    for cfg in configs:
        buckets.setdefault(_netplan_section(cfg.kind), []).append(cfg)
    lines = ["network:", "  version: 2"]
    for section in ("ethernets", "bonds", "bridges", "vlans"):
        section_cfgs = buckets.get(section)
        if not section_cfgs:
            continue
        lines.append(f"  {section}:")
        for cfg in section_cfgs:
            lines += _netplan_device(cfg)
    return "\n".join(lines) + "\n"


def _netplan_device(cfg: LinkConfig) -> list[str]:
    out = [f"    {cfg.name}:",
           f"      dhcp4: {_yaml_bool(cfg.dhcp4)}",
           f"      dhcp6: {_yaml_bool(cfg.dhcp6)}"]
    if cfg.addresses:
        out.append("      addresses:")
        out += [f"        - {addr}" for addr in cfg.addresses]
    # Modern netplan deprecates gateway4/gateway6 in favour of a default route.
    gateways = [gw for gw in (cfg.gateway4, cfg.gateway6) if gw]
    if gateways:
        out.append("      routes:")
        for via in gateways:
            out += ["        - to: default", f"          via: {via}"]
    if cfg.dns:
        out += ["      nameservers:", "        addresses:"]
        out += [f"          - {server}" for server in cfg.dns]
    return out


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


# ---------------------------------------------------------------------------- #
# ifupdown(2): one drop-in under /etc/network/interfaces.d, reloaded
# ---------------------------------------------------------------------------- #
IFUPDOWN_PATH = "/etc/network/interfaces.d/90-netgrip.cfg"


def ifupdown_file(configs: list[LinkConfig]) -> str:
    """Render an ``/etc/network/interfaces`` drop-in for ifupdown(2).

    One ``iface … inet``/``inet6`` stanza per family per link, using CIDR
    ``address`` lines (ifupdown2 accepts them). Lands in ``interfaces.d``, which
    the stock ``source /etc/network/interfaces.d/*`` line pulls in — so it adds
    to, rather than rewrites, the host's existing /etc/network/interfaces."""
    header = (
        "# Managed by NetGrip. Drop-in sourced from /etc/network/interfaces\n"
        "# via its 'source /etc/network/interfaces.d/*' line. Edit in NetGrip.\n"
    )
    blocks = [_ifupdown_iface(cfg) for cfg in configs]
    return header + "\n" + "\n\n".join(blocks) + "\n"


def _ifupdown_iface(cfg: LinkConfig) -> str:
    lines = [f"auto {cfg.name}"]
    lines += _ifupdown_family(cfg, 4, "inet", cfg.dhcp4, cfg.gateway4)
    lines += _ifupdown_family(cfg, 6, "inet6", cfg.dhcp6, cfg.gateway6)
    return "\n".join(lines)


def _ifupdown_family(cfg: LinkConfig, family: int, method: str, dhcp: bool,
                     gateway: str) -> list[str]:
    addresses = cfg.addresses_for(family)
    if addresses:
        out = [f"iface {cfg.name} {method} static"]
        out += [f"    address {addr}" for addr in addresses]
        if gateway:
            out.append(f"    gateway {gateway}")
        dns = cfg.dns_for(family)
        if dns:
            out.append(f"    dns-nameservers {' '.join(dns)}")
        return out
    if dhcp:
        return [f"iface {cfg.name} {method} dhcp"]
    return []


# ---------------------------------------------------------------------------- #
# NetworkManager: modify the connection profile through nmcli
# ---------------------------------------------------------------------------- #
NM_CONNECTIONS_COMMAND = ["nmcli", "-t", "-f", "DEVICE,NAME", "connection", "show"]


def read_nm_connections(runner) -> dict[str, str]:
    """Map device → active connection name via ``nmcli``. Best-effort: a host
    where nmcli can't run yields ``{}`` (Save then reports the missing
    profile), never an exception — matching the backend probe's contract."""
    try:
        out = runner.run(NM_CONNECTIONS_COMMAND)
    except (RuntimeError, ValueError):
        return {}
    return parse_nm_connections(out)


def parse_nm_connections(text: str) -> dict[str, str]:
    """Parse ``nmcli -t -f DEVICE,NAME connection show`` output.

    The terminal format is colon-separated with colons inside values escaped as
    ``\\:``. Device names never contain a colon, so splitting on the first one
    is safe; a connection name on the right is then unescaped. Rows with no
    device (``--``) are skipped — they aren't attached to a link to Save."""
    conns: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        device, sep, name = line.partition(":")
        device = device.strip()
        if sep and device and device != "--":
            conns[device] = name.replace("\\:", ":")
    return conns


def nmcli_commands(cfg: LinkConfig, connection: str) -> list[list[str]]:
    """Build the ``nmcli con mod`` (+ ``con up``) plan for one link's config.

    Sets each configured family's method (manual with static addresses, else
    auto for a lease) and then **always** its addresses/gateway/DNS — clearing
    them when empty. Clearing matters: NetworkManager keeps applying a profile's
    ``ipv4.addresses`` even under ``method auto``, so switching to DHCP without
    blanking them leaves the old static address stuck on the link (it reappears
    on the next ``con up``). A family with neither static nor DHCP config is
    left untouched. Finally re-activate so the change applies now and on reboot."""
    args: list[str] = []
    args += _nmcli_family(cfg, 4, "ipv4", cfg.dhcp4, cfg.gateway4)
    args += _nmcli_family(cfg, 6, "ipv6", cfg.dhcp6, cfg.gateway6)
    if not args:
        return []
    return [
        ["nmcli", "con", "mod", connection, *args],
        ["nmcli", "con", "up", connection],
    ]


def _nmcli_family(cfg: LinkConfig, family: int, key: str, dhcp: bool,
                  gateway: str) -> list[str]:
    addresses = cfg.addresses_for(family)
    if addresses:
        method = "manual"
    elif dhcp:
        method = "auto"
    else:
        return []  # this family isn't configured here — leave the profile alone
    # Set every property explicitly so the profile *matches* the desired config:
    # an empty value clears a stale static address/gateway/DNS (the bug above).
    return [
        f"{key}.method", method,
        f"{key}.addresses", ",".join(addresses),
        f"{key}.gateway", gateway,
        f"{key}.dns", ",".join(cfg.dns_for(family)),
    ]


# ---------------------------------------------------------------------------- #
# dispatch
# ---------------------------------------------------------------------------- #
def persist_plan(configs: list[LinkConfig], backend: Backend,
                 connections: dict[str, str] | None = None) -> list[list[str]]:
    """The plan that writes ``configs`` through ``backend``.

    Dispatches on the detected backend kind; raises :class:`PersistError` for a
    runtime-only or unknown host (nowhere persistent to write). For
    NetworkManager, ``connections`` must map each link to its connection profile
    (see :func:`read_nm_connections`)."""
    if backend.kind == NETWORKD:
        return _networkd_plan(configs)
    if backend.kind == NETPLAN:
        plan = plan_write_file(NETPLAN_PATH, netplan_yaml(configs))
        # netplan refuses world-readable files (they can hold secrets); apply
        # re-renders and activates the whole configuration.
        plan.append(["chmod", "600", NETPLAN_PATH])
        plan.append(["netplan", "apply"])
        return plan
    if backend.kind == IFUPDOWN:
        plan = plan_write_file(IFUPDOWN_PATH, ifupdown_file(configs))
        plan.append(["ifreload", "-a"])  # ifupdown2: re-read and apply
        return plan
    if backend.kind == NETWORKMANAGER:
        return _nm_plan(configs, connections or {})
    raise PersistError(
        "This host has no persistent network backend (runtime only), so there "
        "is nowhere to Save changes — they apply to the running system only."
    )


def _nm_plan(configs: list[LinkConfig], connections: dict[str, str]) -> list[list[str]]:
    plan: list[list[str]] = []
    for cfg in configs:
        connection = connections.get(cfg.name)
        if not connection:
            raise PersistError(
                f"No NetworkManager connection is active on {cfg.name}, so its "
                "configuration can't be saved through NetworkManager."
            )
        plan += nmcli_commands(cfg, connection)
    return plan


def _cidr_family(cidr: str) -> int | None:
    try:
        return ipaddress.ip_interface(cidr).version
    except ValueError:
        return None
