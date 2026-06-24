"""Command construction in the runners (nothing is executed)."""

import os
import shlex

import pytest

from netgrip.core import runner as runner_mod
from netgrip.core.runner import (
    CommandError,
    LocalRunner,
    SSHRunner,
    UnconnectedRunner,
    batch_script,
    hostkey_failure,
    is_auth_failure,
    offending_hostkey_removal,
)


def test_batch_script_joins_and_quotes():
    script = batch_script([
        ["ip", "address", "add", "192.168.1.10/24", "dev", "eth0"],
        ["ip", "link", "set", "dev", "eth0", "up"],
    ])
    assert script == (
        "ip address add 192.168.1.10/24 dev eth0 && ip link set dev eth0 up"
    )


def test_batch_script_quotes_metacharacters():
    # A hostile value must never be able to escape into the shell.
    script = batch_script([["echo", "; rm -rf /"]])
    assert script == "echo '; rm -rf /'"
    assert shlex.split(script) == ["echo", "; rm -rf /"]


def test_ssh_argv_uses_batchmode_and_path_fallback():
    runner = SSHRunner("user@example")
    argv = runner._ssh_argv("ip -json address show")
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert argv[-2] == "--"
    assert argv[-1].endswith("ip -json address show")
    assert "/usr/sbin" in argv[-1]  # sbin PATH fallback for non-login shells
    assert runner.label == "user@example"


def test_ssh_argv_strict_hostkey_by_default():
    argv = SSHRunner("host")._ssh_argv("id -u")
    assert "StrictHostKeyChecking=yes" in argv


def test_ssh_argv_uses_chosen_hostkey_policy():
    argv = SSHRunner("host", hostkey_policy=SSHRunner.HOSTKEY_ACCEPT_NEW)._ssh_argv("id -u")
    assert "StrictHostKeyChecking=accept-new" in argv
    assert "StrictHostKeyChecking=yes" not in argv


def test_unknown_hostkey_failure_detected():
    msg = (
        "ssh host: id -u failed (exit 255):\n"
        "No ED25519 host key is known for host and you have requested strict "
        "checking.\nHost key verification failed."
    )
    assert hostkey_failure(msg) == "unknown"


def test_changed_hostkey_failure_detected():
    # The "something nasty" warning ssh prints when a stored key no longer matches.
    msg = (
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
        "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
        "IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!\n"
        "Host key for host has changed and you have requested strict checking.\n"
        "Host key verification failed."
    )
    assert hostkey_failure(msg) == "changed"


def test_other_errors_are_not_hostkey_failures():
    assert hostkey_failure("Connection timed out") is None


def test_offending_hostkey_removal_parses_ssh_suggestion():
    msg = (
        "Offending ECDSA key in /home/ross/.ssh/known_hosts:30\n"
        "  remove with:\n"
        "  ssh-keygen -f '/home/ross/.ssh/known_hosts' -R '192.168.1.10'\n"
        "Host key verification failed."
    )
    assert offending_hostkey_removal(msg) == [
        "ssh-keygen", "-f", "/home/ross/.ssh/known_hosts", "-R", "192.168.1.10"
    ]


def test_offending_hostkey_removal_none_without_suggestion():
    assert offending_hostkey_removal("Host key verification failed.") is None


def test_hostkey_removal_argv_prefers_ssh_suggestion():
    msg = "ssh-keygen -f '/home/ross/.ssh/known_hosts' -R '10.0.0.9'"
    argv = SSHRunner("admin@10.0.0.9").hostkey_removal_argv(msg)
    assert argv == ["ssh-keygen", "-f", "/home/ross/.ssh/known_hosts", "-R", "10.0.0.9"]


def test_hostkey_removal_argv_falls_back_to_bare_host():
    argv = SSHRunner("admin@192.168.1.10").hostkey_removal_argv(None)
    assert argv[:2] == ["ssh-keygen", "-f"]
    assert argv[-2:] == ["-R", "192.168.1.10"]  # user@ stripped


def test_auth_failure_detected():
    # Password available: offer the dialog.
    assert is_auth_failure("admin@10.0.0.1: Permission denied (publickey,password).")
    assert is_auth_failure("Permission denied, please try again.")
    assert is_auth_failure("Permission denied (publickey,keyboard-interactive).")
    # Publickey-only server: don't offer a password that can't work.
    assert not is_auth_failure("Permission denied (publickey).")
    assert not is_auth_failure("Host key verification failed.")
    assert not is_auth_failure("Connection refused")


def test_password_switches_off_batchmode_and_sets_askpass_env():
    runner = SSHRunner("admin@10.0.0.1")
    # Key-only by default: BatchMode on, no askpass env.
    assert "BatchMode=yes" in runner._ssh_argv("id -u")
    assert runner._ssh_env() is None
    assert not runner.had_password()

    runner.set_password("hunter2")
    argv = runner._ssh_argv("id -u")
    assert "BatchMode=no" in argv
    assert "BatchMode=yes" not in argv
    env = runner._ssh_env()
    assert env is not None
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env["NETGRIP_ASKPASS"] == "hunter2"
    assert "askpass" in os.path.basename(env["SSH_ASKPASS"])
    assert runner.had_password()


def test_clearing_password_restores_batchmode():
    runner = SSHRunner("admin@10.0.0.1")
    runner.set_password("hunter2")
    runner.set_password(None)
    assert "BatchMode=yes" in runner._ssh_argv("id -u")
    assert runner._ssh_env() is None
    assert not runner.had_password()


def test_askpass_helper_holds_no_secret():
    runner = SSHRunner("admin@10.0.0.1")
    runner.set_password("hunter2")
    path = runner._ssh_env()["SSH_ASKPASS"]
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    assert "hunter2" not in body  # secret comes from the env, not the script
    if os.name != "nt":
        # Windows doesn't have Unix permission bits; the .cmd helper is
        # readable by the current user only through NTFS ACLs, not st_mode.
        assert oct(os.stat(path).st_mode)[-3:] == "700"


def test_windows_askpass_is_a_secret_free_cmd_helper(monkeypatch):
    # On Windows ssh can only launch a real program, so the helper is a .cmd
    # batch file that echoes the password env var — still nothing on disk.
    monkeypatch.setattr(runner_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(runner_mod, "_askpass_path", None)  # don't reuse a POSIX helper
    monkeypatch.delenv("DISPLAY", raising=False)
    runner = SSHRunner("admin@10.0.0.1")
    runner.set_password("hunter2")
    env = runner._ssh_env()
    helper = env["SSH_ASKPASS"]
    assert helper.endswith(".cmd")
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
    assert "DISPLAY" not in env  # no X11 display invented on Windows
    with open(helper, encoding="utf-8") as fh:
        body = fh.read()
    assert "hunter2" not in body
    assert "NETGRIP_ASKPASS" in body


def test_local_runner_refuses_privileged_on_windows(monkeypatch):
    monkeypatch.setattr(runner_mod, "IS_WINDOWS", True)
    with pytest.raises(CommandError) as exc:
        LocalRunner().run_privileged([["ip", "link", "set", "eth0", "up"]])
    assert "Windows" in str(exc.value)


def test_unconnected_runner_refuses_everything():
    runner = UnconnectedRunner()
    with pytest.raises(CommandError):
        runner.run(["ip", "addr"])
    with pytest.raises(CommandError):
        runner.run_privileged([["ip", "link", "set", "eth0", "up"]])


# -- local escalation / sudo password caching ------------------------------ #

def test_sudo_message_classifiers():
    assert runner_mod.sudo_needs_password("sudo: a password is required")
    assert runner_mod.sudo_needs_password("sudo: a terminal is required to read the password")
    assert not runner_mod.sudo_needs_password("user is not in the sudoers file")
    assert runner_mod.sudo_auth_failed("sudo: 3 incorrect password attempts")
    assert runner_mod.sudo_auth_failed("Sorry, try again.")  # sudo -S over SSH
    assert not runner_mod.sudo_auth_failed("Cannot assign requested address")


class _FakeProc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _local_runner(monkeypatch, *, sudo_rc, sudo_stderr="", which=("sudo",), display=True):
    """A LocalRunner whose `sudo -n true` probe and `which` are stubbed, so the
    escalation choice is exercised without a real sudo/pkexec on the test host."""
    monkeypatch.setattr(runner_mod.os, "geteuid", lambda: 1000)
    tools = {name: f"/usr/bin/{name}" for name in which}
    monkeypatch.setattr(runner_mod.shutil, "which", lambda name: tools.get(name))
    monkeypatch.setattr(runner_mod.subprocess, "run",
                        lambda *a, **k: _FakeProc(sudo_rc, sudo_stderr))
    if display:
        monkeypatch.setenv("DISPLAY", ":0")
    else:
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return LocalRunner()


def test_local_prefers_passwordless_sudo(monkeypatch):
    runner = _local_runner(monkeypatch, sudo_rc=0)
    assert runner.escalation_status() == "ready"
    assert runner._pick_escalation() == ["sudo", "-n"]


def test_local_needs_password_then_caches_and_wires_askpass(monkeypatch):
    runner = _local_runner(monkeypatch, sudo_rc=1, sudo_stderr="sudo: a password is required")
    assert runner.escalation_status() == "needs_password"

    runner.set_password("hunter2")
    assert runner.had_password()
    assert runner.escalation_status() == "ready"
    assert runner._pick_escalation() == ["sudo", "-A"]
    # The secret reaches sudo only through the askpass env, never the argv.
    env = runner._escalation_env(["sudo", "-A"])
    assert env["SUDO_ASKPASS"]
    assert env[runner_mod._ASKPASS_ENV] == "hunter2"
    assert "hunter2" not in runner._pick_escalation()

    runner.set_password(None)  # a wrong password is cleared -> back to prompting
    assert runner.escalation_status() == "needs_password"


def test_local_non_sudoer_falls_back_to_pkexec(monkeypatch):
    runner = _local_runner(
        monkeypatch, sudo_rc=1, sudo_stderr="user is not in the sudoers file",
        which=("sudo", "pkexec"),
    )
    assert runner.escalation_status() == "ready"
    assert runner._pick_escalation() == ["pkexec"]


def test_local_escalation_unavailable_without_sudo_or_pkexec(monkeypatch):
    runner = _local_runner(monkeypatch, sudo_rc=1, which=(), display=False)
    assert runner.escalation_status() == "unavailable"


# -- remote (SSH) escalation / sudo password over the wire ------------------ #

def _fake_ssh(monkeypatch, respond):
    """An SSHRunner whose `_run_remote` is stubbed by ``respond(remote_command)``
    (returning stdout or raising CommandError), so escalation and the sudo
    write-through are exercised without a real ssh. Returns the runner and a list
    of (remote_command, stdin_text) calls for assertions."""
    runner = SSHRunner("admin@10.0.0.1")
    calls = []

    def fake(remote_command, *, timeout=runner_mod.READ_TIMEOUT, stdin_text=None):
        calls.append((remote_command, stdin_text))
        return respond(remote_command)

    monkeypatch.setattr(runner, "_run_remote", fake)
    return runner, calls


def _ssh_responder(uid="1000", sudo_n=None):
    """Build a responder: `id -u` returns ``uid``; `sudo -n true` succeeds unless
    ``sudo_n`` is an exception to raise; anything else (the script) returns ''."""
    def respond(cmd):
        if cmd == "id -u":
            return uid
        if cmd == "sudo -n true":
            if sudo_n is not None:
                raise sudo_n
            return ""
        return ""
    return respond


def test_ssh_root_login_is_ready(monkeypatch):
    runner, _ = _fake_ssh(monkeypatch, _ssh_responder(uid="0"))
    assert runner.escalation_status() == "ready"


def test_ssh_passwordless_sudo_is_ready_and_cached(monkeypatch):
    runner, calls = _fake_ssh(monkeypatch, _ssh_responder())
    assert runner.escalation_status() == "ready"
    calls.clear()
    # Cached: a second check re-probes neither identity nor `sudo -n true`.
    assert runner.escalation_status() == "ready"
    assert calls == []


def test_ssh_needs_password_then_caches_and_clears(monkeypatch):
    needs = CommandError("sudo -n true", 1, "sudo: a password is required")
    runner, _ = _fake_ssh(monkeypatch, _ssh_responder(sudo_n=needs))
    assert runner.escalation_status() == "needs_password"
    assert not runner.had_sudo_password()

    runner.set_sudo_password("hunter2")
    assert runner.had_sudo_password()
    assert runner.escalation_status() == "ready"

    runner.set_sudo_password(None)  # a wrong password is cleared -> prompt again
    assert runner.escalation_status() == "needs_password"


def test_ssh_non_sudoer_is_unavailable(monkeypatch):
    nope = CommandError("sudo -n true", 1, "admin is not in the sudoers file")
    runner, _ = _fake_ssh(monkeypatch, _ssh_responder(sudo_n=nope))
    assert runner.escalation_status() == "unavailable"


def test_ssh_privileged_pipes_sudo_password_via_stdin(monkeypatch):
    runner, calls = _fake_ssh(monkeypatch, _ssh_responder())
    runner.set_sudo_password("hunter2")
    runner.run_privileged([["ip", "link", "set", "eth0", "up"]])
    remote, stdin_text = calls[-1]
    assert remote.startswith("sudo -S -p '' sh -c ")
    assert "ip link set eth0 up" in remote
    # The password travels only on stdin, never in the command line.
    assert stdin_text == "hunter2\n"
    assert "hunter2" not in remote


def test_ssh_privileged_as_root_runs_without_sudo(monkeypatch):
    runner, calls = _fake_ssh(monkeypatch, _ssh_responder(uid="0"))
    runner.run_privileged([["ip", "link", "set", "eth0", "up"]])
    remote, stdin_text = calls[-1]
    assert remote == "ip link set eth0 up"
    assert stdin_text is None


def test_ssh_privileged_without_password_uses_sudo_n(monkeypatch):
    runner, calls = _fake_ssh(monkeypatch, _ssh_responder())
    runner.run_privileged([["ip", "link", "set", "eth0", "up"]])
    remote, stdin_text = calls[-1]
    assert remote.startswith("sudo -n sh -c ")
    assert stdin_text is None
