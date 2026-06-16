"""Execute commands on the managed host, locally or over SSH.

Reads run as the invoking user. Writes go through :meth:`Runner.run_privileged`,
which batches a whole user action (e.g. "move this address") into a single
shell invocation so privilege escalation prompts at most once per action.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod

READ_TIMEOUT = 30
WRITE_TIMEOUT = 60

# `ip` lives in sbin on some distros, which user sessions often lack in PATH.
_EXTRA_PATH = "/usr/sbin:/sbin:/usr/local/sbin"

# NetGrip runs on Windows purely as an SSH client: there is no local `ip`, no
# sudo/pkexec and no managed localhost there, so local management is disabled
# and only the SSH path is offered.
IS_WINDOWS = os.name == "nt"

# Keep the helper ssh/ssh-keygen processes from flashing a console window when
# NetGrip is launched as a GUI app on Windows (no-op elsewhere).
_POPEN_EXTRA = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}


class CommandError(RuntimeError):
    def __init__(self, command: str, returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"`{command}` failed (exit {returncode}):\n{self.stderr}")


def batch_script(commands: list[list[str]]) -> str:
    """Join several argv lists into one `&&`-chained shell script."""
    return " && ".join(shlex.join(argv) for argv in commands)


def hostkey_failure(message: str) -> str | None:
    """Classify an ssh host-key rejection from its error text.

    Returns "changed" when the stored key no longer matches (the scary
    "REMOTE HOST IDENTIFICATION HAS CHANGED … SOMETHING NASTY" warning),
    "unknown" for a first-time host whose key isn't in known_hosts, or None
    when the failure isn't about host keys at all. Both kinds happen under
    BatchMode, where ssh can't prompt to confirm a fingerprint and so refuses.
    """
    lowered = message.lower()
    if "host key verification failed" not in lowered:
        return None
    if "identification has changed" in lowered or "host key for" in lowered:
        return "changed"
    return "unknown"


# ssh prints, verbatim, the command to clear a stale key, e.g.:
#   ssh-keygen -f '/home/ross/.ssh/known_hosts' -R '192.168.1.10'
_REMOVE_HOSTKEY_RE = re.compile(r"ssh-keygen -f '([^']+)' -R '([^']+)'")


def offending_hostkey_removal(message: str) -> list[str] | None:
    """The local `ssh-keygen -R` argv ssh itself suggests for a changed key.

    Parsing ssh's own "remove with:" line gives the exact known_hosts file and
    host name it matched, which is more reliable than reconstructing them from
    the connection string (which may be a config alias or carry a user@).
    """
    match = _REMOVE_HOSTKEY_RE.search(message)
    if not match:
        return None
    return ["ssh-keygen", "-f", match.group(1), "-R", match.group(2)]


def is_auth_failure(message: str) -> bool:
    """True if ssh failed with credentials AND password auth is worth trying.

    SSH error messages list the methods the server still accepts, e.g.
    ``Permission denied (publickey,password).``  Offering a password dialog
    when ``password`` is absent (server is publickey-only) would be misleading,
    so we only return True when the message indicates password or
    keyboard-interactive auth is available.
    """
    lowered = message.lower()
    if "permission denied" not in lowered:
        return False
    return any(m in lowered for m in ("password", "keyboard-interactive", "please try again"))


# Env var the askpass helper echoes. The password lives only in the ssh
# process environment (same-user readable), never in the script or on disk.
_ASKPASS_ENV = "NETGRIP_SSH_PASSWORD"
_askpass_path: str | None = None


def _askpass_helper() -> str:
    """Path to a tiny, secret-free SSH_ASKPASS helper, created once per run.

    It is written into a private temp dir (mkdtemp, so no symlink race) and
    merely echoes whatever is in ``$NETGRIP_SSH_PASSWORD`` — the password lives
    only in the ssh process environment, never on disk. On Windows the helper is
    a ``.cmd`` batch file (the form ssh can launch there); elsewhere it is a
    0700 ``/bin/sh`` script.
    """
    global _askpass_path
    if _askpass_path and os.path.exists(_askpass_path):
        return _askpass_path
    directory = tempfile.mkdtemp(prefix="netgrip-")
    if IS_WINDOWS:
        path = os.path.join(directory, "askpass.cmd")
        # Delayed expansion (!VAR!) so cmd metacharacters in the password
        # (& | < > ^ %) are echoed literally instead of being re-parsed.
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(
                f"@echo off\nsetlocal EnableDelayedExpansion\necho(!{_ASKPASS_ENV}!\n"
            )
    else:
        path = os.path.join(directory, "askpass")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nexec printf \'%s\\n\' "${_ASKPASS_ENV}"\n')
        os.chmod(path, 0o700)
    _askpass_path = path
    return path


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
        if IS_WINDOWS:
            raise CommandError(
                batch_script(commands),
                1,
                "NetGrip can't manage the local machine on Windows; connect to a "
                "Linux host over SSH instead.",
            )
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
    By default BatchMode keeps ssh from hanging on interactive prompts; if the
    user supplies a password we drop BatchMode and answer the prompt through an
    SSH_ASKPASS helper instead (see set_password).
    """

    # Default: refuse unknown/changed keys (ssh prompts can't work under
    # BatchMode). After asking the user, the UI may relax this to "accept-new"
    # for the session, which records a first-time key (and, once a stale key
    # has been cleared with forget_hostkey, re-learns a changed one).
    HOSTKEY_STRICT = "yes"
    HOSTKEY_ACCEPT_NEW = "accept-new"

    def __init__(self, host: str, hostkey_policy: str = HOSTKEY_STRICT) -> None:
        self.host = host
        self.label = host
        self.hostkey_policy = hostkey_policy
        self._password: str | None = None
        self._remote_uid: int | None = None

    def set_password(self, password: str | None) -> None:
        """Use `password` for login (None reverts to key/agent-only, BatchMode)."""
        self._password = password or None
        self._remote_uid = None  # re-probe identity under the new credentials

    def had_password(self) -> bool:
        """True if a password is already in use (so a fresh failure is a retry)."""
        return self._password is not None

    def _ssh_argv(self, remote_command: str) -> list[str]:
        # With a password we must let ssh prompt (BatchMode would suppress it);
        # the prompt is answered non-interactively by the askpass helper.
        batch_mode = "no" if self._password else "yes"
        return [
            "ssh",
            "-o", f"BatchMode={batch_mode}",
            "-o", "ConnectTimeout=10",
            "-o", f"StrictHostKeyChecking={self.hostkey_policy}",
            "-o", "NumberOfPasswordPrompts=1",
            # Detect dead connections: if the server stops responding, give up
            # after two missed keepalives (~10 s) rather than hanging until the
            # Python subprocess timeout fires.
            "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2",
            self.host,
            "--",
            # Remote non-interactive shells often lack sbin in PATH.
            f"PATH=$PATH:{_EXTRA_PATH}; {remote_command}",
        ]

    def _ssh_env(self) -> dict[str, str] | None:
        """Environment for ssh: point SSH_ASKPASS at our helper when using a password."""
        if not self._password:
            return None
        env = dict(os.environ)
        env["SSH_ASKPASS"] = _askpass_helper()
        env["SSH_ASKPASS_REQUIRE"] = "force"  # use askpass even with a tty present
        if not IS_WINDOWS:
            env.setdefault("DISPLAY", ":0")  # older ssh only consults askpass if DISPLAY is set
        env[_ASKPASS_ENV] = self._password
        return env

    def hostkey_removal_argv(self, message: str | None = None) -> list[str]:
        """Local ssh-keygen command to drop this host's stale known_hosts key.

        Prefers the exact command ssh suggested in `message`; otherwise targets
        the default known_hosts file and the bare host name (no `user@`).
        """
        if message:
            suggested = offending_hostkey_removal(message)
            if suggested:
                return suggested
        known_hosts = os.path.expanduser("~/.ssh/known_hosts")
        host = self.host.rsplit("@", 1)[-1]
        return ["ssh-keygen", "-f", known_hosts, "-R", host]

    def forget_hostkey(self, message: str | None = None) -> None:
        """Remove the stale host key locally so the next connect can re-learn it."""
        subprocess.run(
            self.hostkey_removal_argv(message),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=READ_TIMEOUT,
            **_POPEN_EXTRA,
        )

    def _run_remote(self, remote_command: str, *, timeout: int = READ_TIMEOUT) -> str:
        argv = self._ssh_argv(remote_command)
        try:
            proc = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._ssh_env(),
                # Detach from any controlling terminal so ssh asks the askpass
                # helper for the password instead of trying to read a tty.
                start_new_session=self._password is not None,
                **_POPEN_EXTRA,
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
            return self._run_remote(script, timeout=WRITE_TIMEOUT)
        # -n: never prompt. An interactive password prompt cannot work through
        # BatchMode ssh, so passwordless sudo (or root login) is required.
        return self._run_remote("sudo -n sh -c " + shlex.quote(script), timeout=WRITE_TIMEOUT)


class UnconnectedRunner(Runner):
    """No host chosen yet — the startup state where local management is absent
    (e.g. Windows). The UI special-cases it: refreshing shows an empty canvas
    and a "pick a host" prompt rather than probing anything."""

    label = "no host"

    def run(self, argv: list[str]) -> str:
        raise CommandError(
            shlex.join(argv), 1, "No host selected — choose one to connect over SSH."
        )

    def run_privileged(self, commands: list[list[str]]) -> str:
        raise CommandError(batch_script(commands), 1, "No host selected.")


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
