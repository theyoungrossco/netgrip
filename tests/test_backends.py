"""Detection of the host's persistent network-config owner (backends.py)."""

from netgrip.core.backends import (
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


def _output(nm: str = "", networkd: str = "", netplan: str = "") -> str:
    """Build a detection-script transcript with the three marker sections."""
    return (
        f"@@NM@@\n{nm}\n"
        f"@@NETWORKD@@\n{networkd}\n"
        f"@@NETPLAN@@\n{netplan}\n"
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


def test_runtime_only_when_nothing_manages():
    backend = parse_backend(_output(nm="inactive", networkd="inactive"))
    assert backend.kind == RUNTIME
    assert backend.persists is False
    assert backend.manages_config is False


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
