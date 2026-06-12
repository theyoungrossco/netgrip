"""Execute commands on the managed host, locally or over SSH.

Reads run as the invoking user. Writes go through :meth:`Runner.run_privileged`,
which batches a whole user action (e.g. "move this address") into a single
shell invocation so privilege escalation prompts at most once per action.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod

READ_TIMEOUT = 30
WRITE_TIMEOUT = 60

# `ip` lives in sbin on some distros, which user sessions often lack in PATH.
_EXTRA_PATH = "/usr/sbin:/sbin:/usr/local/sbin"


class CommandError(RuntimeError):
    def __init__(self, command: str, returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"`{command}` failed (exit {returncode}):\n{self.stderr}")


def batch_script(commands: list[list[str]]) -> str:
    """Join several argv lists into one `&&`-chained shell script."""
    return " && ".join(shlex.join(argv) for argv in commands)


class Runner(ABC):
    """Executes commands on one host."""

    label: str = "?"

    @abstractmethod
    def run(self, argv: list[str]) -> str:
        """Run a read-only command, return stdout. Raises CommandError."""

    @abstractmethod
    def run_privileged(self, commands: list[list[str]]) -> str:
        """Run mutating commands as root, as a single batch."""

    def close(self) -> None:  # noqa: B027 - optional hook, most runners hold no resources
        pass


class LocalRunner(Runner):
    label = "local"

    def __init__(self) -> None:
        self._escalation: list[str] | None = None
        env = dict(os.environ)
        env["PATH"] = env.get("PATH", "") + os.pathsep + _EXTRA_PATH
        self._env = env

    def run(self, argv: list[str]) -> str:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=READ_TIMEOUT, env=self._env
            )
        except FileNotFoundError as exc:
            raise CommandError(shlex.join(argv), 127, str(exc)) from exc
        if proc.returncode != 0:
            raise CommandError(shlex.join(argv), proc.returncode, proc.stderr or proc.stdout)
        return proc.stdout

    def run_privileged(self, commands: list[list[str]]) -> str:
        if not commands:
            return ""
        if os.geteuid() == 0:
            return "".join(self.run(argv) for argv in commands)
        wrapper = self._pick_escalation()
        script = batch_script(commands)
        try:
            proc = subprocess.run(
                [*wrapper, "sh", "-c", script],
                capture_output=True,
                text=True,
                timeout=WRITE_TIMEOUT,
                env=self._env,
            )
        except FileNotFoundError as exc:
            raise CommandError(script, 127, str(exc)) from exc
        if proc.returncode != 0:
            raise CommandError(script, proc.returncode, proc.stderr or proc.stdout)
        return proc.stdout

    def _pick_escalation(self) -> list[str]:
        if self._escalation is not None:
            return self._escalation
        if shutil.which("sudo"):
            check = subprocess.run(["sudo", "-n", "true"], capture_output=True)
            if check.returncode == 0:
                self._escalation = ["sudo", "-n"]
                return self._escalation
        has_display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        if shutil.which("pkexec") and has_display:
            self._escalation = ["pkexec"]
            return self._escalation
        raise CommandError(
            "privilege escalation",
            1,
            "Cannot gain root: run netgrip as root, configure passwordless sudo "
            "for your user, or install polkit (pkexec).",
        )


class SSHRunner(Runner):
    """Runs commands on a remote host through the system `ssh` client.

    Shelling out (rather than using a Python SSH library) means the user's
    ~/.ssh/config, agent, keys, jump hosts and known_hosts all work untouched.
    BatchMode keeps ssh from hanging on interactive prompts.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        self.label = host
        self._remote_uid: int | None = None

    def _ssh_argv(self, remote_command: str) -> list[str]:
        return [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            self.host,
            "--",
            # Remote non-interactive shells often lack sbin in PATH.
            f"PATH=$PATH:{_EXTRA_PATH}; {remote_command}",
        ]

    def _run_remote(self, remote_command: str) -> str:
        argv = self._ssh_argv(remote_command)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=WRITE_TIMEOUT
            )
        except FileNotFoundError as exc:
            raise CommandError("ssh", 127, "ssh client not found on this machine") from exc
        if proc.returncode != 0:
            raise CommandError(
                f"ssh {self.host}: {remote_command}",
                proc.returncode,
                proc.stderr or proc.stdout,
            )
        return proc.stdout

    def run(self, argv: list[str]) -> str:
        return self._run_remote(shlex.join(argv))

    def _remote_is_root(self) -> bool:
        if self._remote_uid is None:
            self._remote_uid = int(self._run_remote("id -u").strip() or "-1")
        return self._remote_uid == 0

    def run_privileged(self, commands: list[list[str]]) -> str:
        if not commands:
            return ""
        script = batch_script(commands)
        if self._remote_is_root():
            return self._run_remote(script)
        # -n: never prompt. An interactive password prompt cannot work through
        # BatchMode ssh, so passwordless sudo (or root login) is required.
        return self._run_remote("sudo -n sh -c " + shlex.quote(script))


class DemoRunner(Runner):
    """Backs the built-in demo host: probing returns canned data, writes refuse."""

    label = "demo"

    def run(self, argv: list[str]) -> str:
        raise CommandError(shlex.join(argv), 1, "Demo mode does not execute commands.")

    def run_privileged(self, commands: list[list[str]]) -> str:
        raise CommandError(
            batch_script(commands),
            1,
            "Demo mode: this is exactly what netgrip would have run on a real host, "
            "but changes are disabled here.",
        )
