"""Command construction in the runners (nothing is executed)."""

import shlex

from netgrip.core.runner import SSHRunner, batch_script


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
