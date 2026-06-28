"""Unit tests for the CLI subcommands (netgrip.cli). No Qt, no real commands."""

from __future__ import annotations

import json
import types

from netgrip.cli import cmd_apply, cmd_backend, cmd_plan, cmd_show
from netgrip.core.backends import NETWORKMANAGER, RUNTIME, Backend
from netgrip.core.runner import CommandError


class StubRunner:
    label = "stub"

    def run(self, cmd):
        return ""

    def run_privileged(self, plan):
        return ""


def _args(**kwargs):
    """Build a simple namespace for passing to cmd_* functions."""
    defaults = {"json": False, "demo": False, "confirm": False}
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# cmd_show
# ---------------------------------------------------------------------------

def test_cmd_show_demo_text(capsys):
    rc = cmd_show(StubRunner(), _args(demo=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "eth0" in out
    assert "lo" in out


def test_cmd_show_demo_json(capsys):
    rc = cmd_show(StubRunner(), _args(demo=True, json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    names = [d["name"] for d in data]
    assert "eth0" in names
    assert "lo" in names
    eth0 = next(d for d in data if d["name"] == "eth0")
    assert eth0["state"] == "up"
    assert eth0["kind"] == "physical"
    assert "addresses" in eth0
    assert isinstance(eth0["gateways"], dict)


def test_cmd_show_command_error_returns_1(capsys, monkeypatch):
    def _bad_probe(runner):
        raise CommandError("ip", 1, "boom")

    import netgrip.core.probe as probe_mod
    monkeypatch.setattr(probe_mod, "probe", _bad_probe)

    rc = cmd_show(StubRunner(), _args(demo=False))
    assert rc == 1


# ---------------------------------------------------------------------------
# cmd_backend
# ---------------------------------------------------------------------------

def test_cmd_backend_text(capsys, monkeypatch):
    monkeypatch.setattr(
        "netgrip.cli.detect_backend",
        lambda runner: Backend(NETWORKMANAGER, "NM owns this host's connections."),
    )
    rc = cmd_backend(StubRunner(), _args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "NetworkManager" in out
    assert "yes" in out


def test_cmd_backend_json(capsys, monkeypatch):
    monkeypatch.setattr(
        "netgrip.cli.detect_backend",
        lambda runner: Backend(RUNTIME, "Nothing manages the host."),
    )
    rc = cmd_backend(StubRunner(), _args(json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["kind"] == RUNTIME
    assert data["persists"] is False
    assert "label" in data
    assert "summary" in data


# ---------------------------------------------------------------------------
# cmd_plan — each op
# ---------------------------------------------------------------------------

def test_plan_up(capsys):
    rc = cmd_plan(_args(op="up", iface="eth0"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ip link set dev eth0 up" in out


def test_plan_down(capsys):
    rc = cmd_plan(_args(op="down", iface="eth0"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ip link set dev eth0 down" in out


def test_plan_set_mtu(capsys):
    rc = cmd_plan(_args(op="set-mtu", iface="eth0", mtu="9000"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "mtu 9000" in out


def test_plan_set_mac(capsys):
    rc = cmd_plan(_args(op="set-mac", iface="eth0", mac="02:00:00:00:00:01"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "address 02:00:00:00:00:01" in out


def test_plan_add_addr(capsys):
    rc = cmd_plan(_args(op="add-addr", iface="eth0", cidr="10.0.0.5/24"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "address add 10.0.0.5/24 dev eth0" in out


def test_plan_del_addr(capsys):
    rc = cmd_plan(_args(op="del-addr", iface="eth0", cidr="10.0.0.5/24"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "address del 10.0.0.5/24 dev eth0" in out


def test_plan_json_output(capsys):
    rc = cmd_plan(_args(op="up", iface="eth0", json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["op"] == "up"
    assert isinstance(data["commands"], list)
    assert data["commands"][0][0] == "ip"


# ---------------------------------------------------------------------------
# cmd_plan — invalid args return 1
# ---------------------------------------------------------------------------

def test_plan_invalid_mtu(capsys):
    rc = cmd_plan(_args(op="set-mtu", iface="eth0", mtu="notanumber"))
    assert rc == 1


def test_plan_zero_mtu(capsys):
    rc = cmd_plan(_args(op="set-mtu", iface="eth0", mtu="0"))
    assert rc == 1


def test_plan_invalid_mac(capsys):
    rc = cmd_plan(_args(op="set-mac", iface="eth0", mac="zz:zz:zz:zz:zz:zz"))
    assert rc == 1


def test_plan_multicast_mac_rejected(capsys):
    # Low bit of first octet set → multicast, kernel rejects it
    rc = cmd_plan(_args(op="set-mac", iface="eth0", mac="01:00:00:00:00:01"))
    assert rc == 1


def test_plan_invalid_cidr(capsys):
    rc = cmd_plan(_args(op="add-addr", iface="eth0", cidr="not-a-cidr"))
    assert rc == 1


# ---------------------------------------------------------------------------
# cmd_apply
# ---------------------------------------------------------------------------

def test_apply_without_confirm_prints_plan_and_does_not_run(capsys):
    runner = StubRunner()
    called = []
    runner.run_privileged = lambda plan: called.append(plan)

    rc = cmd_apply(runner, _args(op="up", iface="eth0", confirm=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ip link set dev eth0 up" in out
    assert "--confirm" in out
    assert called == []


def test_apply_with_confirm_calls_run_privileged(capsys):
    runner = StubRunner()
    called = []
    runner.run_privileged = lambda plan: called.append(plan) or ""

    rc = cmd_apply(runner, _args(op="up", iface="eth0", confirm=True))
    assert rc == 0
    assert called != []
    plan = called[0]
    assert plan[0] == ["ip", "link", "set", "dev", "eth0", "up"]


def test_apply_command_error_returns_1(capsys):
    runner = StubRunner()
    runner.run_privileged = lambda plan: (_ for _ in ()).throw(
        CommandError("ip", 1, "permission denied")
    )
    rc = cmd_apply(runner, _args(op="down", iface="eth0", confirm=True))
    assert rc == 1


def test_apply_invalid_args_returns_1_without_running(capsys):
    runner = StubRunner()
    called = []
    runner.run_privileged = lambda plan: called.append(plan)
    rc = cmd_apply(runner, _args(op="set-mac", iface="eth0", mac="bad", confirm=True))
    assert rc == 1
    assert called == []
