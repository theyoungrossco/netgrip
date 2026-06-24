"""Detection of the host's persistent network-config owner (backends.py)."""

from netgrip.core.backends import (
    IFUPDOWN,
    NETPLAN,
    NETWORKD,
    NETWORKMANAGER,
    RUNTIME,
    UNKNOWN,
    Backend,
    detect_backend,
    parse_backend,
)
from netgrip.core.runner import CommandError, Runner


def _output(nm: str = "", networkd: str = "", netplan: str = "", ifupdown: str = "") -> str:
    """Build a detection-script transcript with the marker sections."""
    return (
        f"@@NM@@\n{nm}\n"
        f"@@NETWORKD@@\n{networkd}\n"
        f"@@NETPLAN@@\n{netplan}\n"
        f"@@IFUPDOWN@@\n{ifupdown}\n"
    )


def test_networkmanager_active_wins():
    backend = parse_backend(_output(nm="active", networkd="inactive"))
    assert backend.kind == NETWORKMANAGER
    assert backend.label == "NetworkManager"
    assert backend.persists is True
    assert backend.manages_config is True


def test_networkmanager_wins_even_with_netplan_present():
    # A netplan-rendered desktop delegates to NM; NM still owns the live config.
    backend = parse_backend(_output(nm="active", netplan="01-network-manager-all.yaml"))
    assert backend.kind == NETWORKMANAGER
    assert "netplan" in backend.summary


def test_netplan_over_networkd():
    backend = parse_backend(
        _output(nm="inactive", networkd="active", netplan="01-netcfg.yaml\n50-cloud.yaml")
    )
    assert backend.kind == NETPLAN
    assert backend.persists is True
    assert "systemd-networkd" in backend.summary
    assert "2 files" in backend.summary


def test_netplan_non_yaml_files_ignored():
    # README or a stray dotfile in /etc/netplan must not count as netplan config.
    backend = parse_backend(_output(networkd="active", netplan="README\n.keep"))
    assert backend.kind == NETWORKD


def test_networkd_only():
    backend = parse_backend(_output(nm="inactive", networkd="active"))
    assert backend.kind == NETWORKD
    assert backend.persists is True


def test_ifupdown_when_interfaces_file_and_ifreload():
    # Debian/Proxmox: /etc/network/interfaces present, ifupdown2's ifreload there.
    backend = parse_backend(_output(ifupdown="hasfile\nifreload"))
    assert backend.kind == IFUPDOWN
    assert backend.persists is True
    assert "/etc/network/interfaces" in backend.summary


def test_ifupdown_from_active_networking_service():
    backend = parse_backend(_output(ifupdown="active\nifreload"))
    assert backend.kind == IFUPDOWN


def test_ifupdown_needs_reload_tool():
    # An interfaces file but no ifreload (classic ifupdown) isn't claimed — the
    # Save write-through drives ifupdown2's ifreload, so we'd have no way to apply.
    backend = parse_backend(_output(ifupdown="hasfile"))
    assert backend.kind == RUNTIME


def test_active_manager_beats_ifupdown():
    # A host running systemd-networkd that also has a stale interfaces file:
    # the active manager wins, not ifupdown.
    backend = parse_backend(_output(networkd="active", ifupdown="hasfile\nifreload"))
    assert backend.kind == NETWORKD


def test_runtime_only_when_nothing_manages():
    backend = parse_backend(_output(nm="inactive", networkd="inactive"))
    assert backend.kind == RUNTIME
    assert backend.persists is False
    assert backend.manages_config is False
    assert backend.install_ifupdown2 is False


def test_runtime_offers_ifupdown2_when_classic_ifupdown_on_apt():
    # Classic ifupdown (interfaces file, no ifreload) on an apt host: runtime
    # only, but installing ifupdown2 would make it writable — the UI offers it.
    backend = parse_backend(_output(ifupdown="hasfile\nhasapt"))
    assert backend.kind == RUNTIME
    assert backend.install_ifupdown2 is True
    assert "ifupdown2" in backend.summary


def test_runtime_no_ifupdown2_offer_without_apt():
    # An interfaces file but no apt (or no interfaces file): nothing to one-click.
    assert parse_backend(_output(ifupdown="hasfile")).install_ifupdown2 is False
    assert parse_backend(_output(ifupdown="hasapt")).install_ifupdown2 is False


def test_runtime_only_when_systemctl_missing():
    # No systemctl: is-active prints nothing; an empty /etc/netplan listing too.
    backend = parse_backend(_output())
    assert backend.kind == RUNTIME


class _FakeRunner(Runner):
    label = "fake"

    def __init__(self, out: str | None = None):
        self._out = out

    def run(self, argv):
        if self._out is None:
            raise CommandError("detect", 1, "boom")
        return self._out

    def run_privileged(self, commands):
        return ""


def test_detect_backend_reads_runner():
    runner = _FakeRunner(_output(nm="active"))
    assert detect_backend(runner).kind == NETWORKMANAGER


def test_detect_backend_is_best_effort_on_failure():
    # A host we cannot read degrades to UNKNOWN, never an exception.
    backend = detect_backend(_FakeRunner(None))
    assert backend.kind == UNKNOWN
    assert backend.persists is False
    assert isinstance(backend, Backend)
