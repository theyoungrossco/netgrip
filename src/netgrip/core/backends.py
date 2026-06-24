"""Detect which subsystem owns a host's persistent network configuration.

NetGrip's mutations are runtime ``ip`` commands today, so whether a change
survives a reboot depends on *what owns the config*: NetworkManager and
systemd-networkd re-assert their stored configuration at boot (and may even
revert a runtime change before then), netplan renders to one of those two, and
a host running none of them keeps only what is in the live kernel. This module
reads those signals — best-effort, exactly like the DNS probe — so the UI can
tell the user, per host, what is in charge and whether a change will persist.

It is the keystone of the 0.2 "persistence" work: the write-through *Save*
backends (NetworkManager / systemd-networkd / netplan / ifupdown) all hang off
the kind detected here. Qt-free, like the rest of ``core``.
"""

from __future__ import annotations

from dataclasses import dataclass

from netgrip.core.runner import Runner

# Stable identifiers for each kind of config owner, with their human labels.
NETWORKMANAGER = "networkmanager"
NETWORKD = "networkd"
NETPLAN = "netplan"
IFUPDOWN = "ifupdown"
RUNTIME = "runtime"
UNKNOWN = "unknown"

KIND_LABELS = {
    NETWORKMANAGER: "NetworkManager",
    NETWORKD: "systemd-networkd",
    NETPLAN: "netplan",
    IFUPDOWN: "ifupdown",
    RUNTIME: "Runtime only",
    UNKNOWN: "Unknown",
}

# Section markers for the one-round-trip detection script, mirroring the style
# of probe.DNS_COMMAND. Every probe is best-effort and the script ends `exit 0`,
# so a host lacking systemctl/netplan yields empty sections rather than failing.
_NM = "@@NM@@"
_NETWORKD = "@@NETWORKD@@"
_NETPLAN = "@@NETPLAN@@"
_IFUPDOWN = "@@IFUPDOWN@@"
DETECT_COMMAND = [
    "sh", "-c",
    f"echo {_NM}; systemctl is-active NetworkManager 2>/dev/null; "
    f"echo {_NETWORKD}; systemctl is-active systemd-networkd 2>/dev/null; "
    f"echo {_NETPLAN}; ls -1 /etc/netplan 2>/dev/null; "
    # ifupdown (Debian/Proxmox /etc/network/interfaces). We only claim it when
    # the reload tool exists — `ifreload` ships with ifupdown2, which is what
    # the Save write-through drives; classic ifupdown without it stays runtime.
    f"echo {_IFUPDOWN}; systemctl is-active networking 2>/dev/null; "
    "test -f /etc/network/interfaces && echo hasfile; "
    "command -v ifreload >/dev/null 2>&1 && echo ifreload; "
    # apt: on a runtime-only Debian/Proxmox host that already has classic
    # ifupdown (an interfaces file) but no ifreload, `apt-get install ifupdown2`
    # is a one-click fix that hands the host a persistence backend. Note whether
    # apt is even here so the UI only offers it where it can work.
    "command -v apt-get >/dev/null 2>&1 && echo hasapt; "
    "exit 0",
]


@dataclass
class Backend:
    """Which subsystem owns this host's network configuration.

    ``kind`` is one of the module constants. ``persists`` says whether a change
    *can be made to survive a reboot* on this host — true wherever a real
    config manager is in charge (NetworkManager / systemd-networkd / netplan),
    false for a pure-runtime host where there is nowhere to write. ``summary``
    is a one-line, user-facing description for the indicator / its tooltip.
    ``install_ifupdown2`` flags a runtime-only host where installing ifupdown2
    would turn the existing classic ifupdown into a writable backend, so the UI
    can offer that as a one-click remediation.
    """

    kind: str
    summary: str = ""
    install_ifupdown2: bool = False

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, KIND_LABELS[UNKNOWN])

    @property
    def manages_config(self) -> bool:
        """True when some manager owns the config (i.e. not pure runtime)."""
        return self.kind in (NETWORKMANAGER, NETWORKD, NETPLAN, IFUPDOWN)

    @property
    def persists(self) -> bool:
        """Whether a *Save* can write persistent config on this host."""
        return self.manages_config


def detect_backend(runner: Runner) -> Backend:
    """Probe ``runner``'s host for its network-config owner.

    Never raises: a host we cannot read (ssh failure, no shell) comes back as
    ``UNKNOWN`` so the UI degrades to "we don't know" rather than erroring — the
    same best-effort contract as :func:`netgrip.core.probe.probe_dns`.
    """
    try:
        out = runner.run(DETECT_COMMAND)
    except (RuntimeError, ValueError):
        return Backend(UNKNOWN, "Could not determine the network configuration backend.")
    return parse_backend(out)


def parse_backend(text: str) -> Backend:
    """Classify the detection script's output into a :class:`Backend`.

    Precedence is deliberate. NetworkManager wins first: where it is active it
    owns the live connections even on a netplan-rendered desktop (whose netplan
    file just delegates to NM), so that is where a change must go. Otherwise a
    populated ``/etc/netplan`` means netplan is the source of truth (rendered by
    systemd-networkd on servers), then a bare active systemd-networkd, then
    ifupdown (Debian/Proxmox ``/etc/network/interfaces``), and finally —
    nothing managing the host — runtime only.
    """
    sections = _split_sections(text)
    nm_active = _is_active(sections.get(_NM, ""))
    networkd_active = _is_active(sections.get(_NETWORKD, ""))
    netplan_files = _netplan_files(sections.get(_NETPLAN, ""))

    if nm_active:
        extra = " (configured via netplan)" if netplan_files else ""
        return Backend(
            NETWORKMANAGER,
            f"NetworkManager owns this host's connections{extra}. "
            "Save writes a persistent connection profile.",
        )
    if netplan_files:
        renderer = "systemd-networkd" if networkd_active else "its renderer"
        plural = "file" if len(netplan_files) == 1 else "files"
        return Backend(
            NETPLAN,
            f"Configured by netplan ({len(netplan_files)} {plural} in /etc/netplan, "
            f"rendered by {renderer}). Save edits netplan and re-applies it.",
        )
    if networkd_active:
        return Backend(
            NETWORKD,
            "systemd-networkd owns this host. Save writes a persistent "
            ".network file under /etc/systemd/network.",
        )
    if _is_ifupdown(sections.get(_IFUPDOWN, "")):
        return Backend(
            IFUPDOWN,
            "Configured by ifupdown (/etc/network/interfaces). Save writes a "
            "drop-in under /etc/network/interfaces.d and runs ifreload.",
        )
    # Runtime only. If classic ifupdown is here (an interfaces file) on an apt
    # host, it is just missing ifreload — installing ifupdown2 makes it a
    # writable backend, which the UI offers as a one-click fix.
    ifupdown_lines = {line.strip() for line in sections.get(_IFUPDOWN, "").splitlines()}
    can_install = "hasfile" in ifupdown_lines and "hasapt" in ifupdown_lines
    summary = (
        "No persistent network manager detected — changes live only in the "
        "running kernel and are lost on reboot."
    )
    if can_install:
        summary += (
            " This host has classic ifupdown; installing ifupdown2 would let "
            "NetGrip Save through it."
        )
    return Backend(RUNTIME, summary, install_ifupdown2=can_install)


def _split_sections(text: str) -> dict[str, str]:
    """Carve the marker-delimited detection output into {marker: block}."""
    sections: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in (_NM, _NETWORKD, _NETPLAN, _IFUPDOWN):
            current = stripped
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return sections


def _is_active(block: str) -> bool:
    """True when ``systemctl is-active`` reported the unit as ``active``."""
    return any(line.strip() == "active" for line in block.splitlines())


def _netplan_files(block: str) -> list[str]:
    """The YAML files an ``ls -1 /etc/netplan`` listing contains."""
    return [
        name for name in (line.strip() for line in block.splitlines())
        if name.endswith((".yaml", ".yml"))
    ]


def _is_ifupdown(block: str) -> bool:
    """True for an ifupdown(2) host we can write to: an /etc/network/interfaces
    file present (or the ``networking`` service active) *and* ``ifreload``
    available, since the Save write-through drives ifupdown2's ifreload."""
    lines = {line.strip() for line in block.splitlines()}
    return ("hasfile" in lines or "active" in lines) and "ifreload" in lines
