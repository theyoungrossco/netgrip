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
    assert is_auth_failure("admin@10.0.0.1: Permission denied (publickey,password).")
    assert is_auth_failure("Permission denied, please try again.")
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
    assert env["NETGRIP_SSH_PASSWORD"] == "hunter2"
    assert env["SSH_ASKPASS"].endswith("askpass")
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
    assert "NETGRIP_SSH_PASSWORD" in body


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
