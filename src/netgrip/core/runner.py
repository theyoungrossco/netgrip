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


class NeedsPassword(Exception):
    """Raised internally when local escalation needs a sudo password we don't
    have yet. The UI catches this (via ``LocalRunner.escalation_status``) to
    prompt for one and cache it, instead of letting the action fail."""


def sudo_needs_password(stderr: str) -> bool:
    """True when ``sudo -n`` failed *because it wanted a password* — i.e. the
    user is a sudoer who just hasn't authenticated, as opposed to not being a
    sudoer at all (in which case we should fall back to pkexec, not prompt)."""
    lowered = stderr.lower()
    return "password is required" in lowered or "a terminal is required" in lowered


def sudo_auth_failed(message: str) -> bool:
    """True when a privileged run failed because the cached sudo password was
    wrong, so the UI can clear it and re-prompt rather than loop on a bad one.
    Covers local sudo ("N incorrect password attempt(s)") and ``sudo -S`` over
    SSH, which rejects the piped password with "Sorry, try again"."""
    lowered = message.lower()
    return "incorrect password" in lowered or "sorry, try again" in lowered


# Env var the askpass helper echoes, shared by the ssh and the local-sudo paths.
# The password lives only in the (same-user readable) process environment of the
# ssh/sudo child, never in a script or on disk.
_ASKPASS_ENV = "NETGRIP_ASKPASS"
_askpass_path: str | None = None


def _askpass_helper() -> str:
    """Path to a tiny, secret-free askpass helper, created once per run.

    Used as SSH_ASKPASS for ssh and SUDO_ASKPASS for local sudo. It is written
    into a private temp dir (mkdtemp, so no symlink race) and merely echoes
    whatever is in ``$NETGRIP_ASKPASS`` — the password lives only in the ssh/sudo
    process environment, never on disk. On Windows the helper is a ``.cmd`` batch
    file (the form ssh can launch there); elsewhere it is a 0700 ``/bin/sh``
    script.
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
        self._password: str | None = None
        env = dict(os.environ)
        env["PATH"] = env.get("PATH", "") + os.pathsep + _EXTRA_PATH
        self._env = env

    def set_password(self, password: str | None) -> None:
        """Cache (or, with None, forget) the sudo password for this session, so
        escalation is authenticated once rather than prompted on every action.
        Forgetting also drops the resolved escalation so it is re-evaluated."""
        self._password = password or None
        self._escalation = None

    def had_password(self) -> bool:
        return self._password is not None

    def escalation_status(self) -> str:
        """How root can be reached right now, without prompting per action:
        ``"ready"`` (already root, passwordless sudo, or a cached password),
        ``"needs_password"`` (a sudoer who must authenticate first), or
        ``"unavailable"`` (no way to escalate). Lets the UI prompt once, up
        front, instead of letting a privileged run fail."""
        if IS_WINDOWS:
            return "unavailable"
        if os.geteuid() == 0:
            return "ready"
        try:
            self._pick_escalation()
        except NeedsPassword:
            return "needs_password"
        except CommandError:
            return "unavailable"
        return "ready"

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
        try:
            wrapper = self._pick_escalation()
        except NeedsPassword as exc:
            raise CommandError(
                batch_script(commands), 1, "An administrator password is required."
            ) from exc
        script = batch_script(commands)
        try:
            proc = subprocess.run(
                [*wrapper, "sh", "-c", script],
                capture_output=True,
                text=True,
                timeout=WRITE_TIMEOUT,
                env=self._escalation_env(wrapper),
            )
        except FileNotFoundError as exc:
            raise CommandError(script, 127, str(exc)) from exc
        if proc.returncode != 0:
            raise CommandError(script, proc.returncode, proc.stderr or proc.stdout)
        return proc.stdout

    def _escalation_env(self, wrapper: list[str]) -> dict[str, str]:
        """Base env, plus the askpass wiring when we drive sudo with a cached
        password (``sudo -A``). The password reaches sudo only through the
        helper's environment, never the command line or disk."""
        if wrapper[:2] != ["sudo", "-A"]:
            return self._env
        env = dict(self._env)
        env["SUDO_ASKPASS"] = _askpass_helper()
        env[_ASKPASS_ENV] = self._password or ""
        return env

    def _pick_escalation(self) -> list[str]:
        if self._escalation is not None:
            return self._escalation
        if shutil.which("sudo"):
            check = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
            if check.returncode == 0:
                self._escalation = ["sudo", "-n"]
                return self._escalation
            # A sudoer who just needs to authenticate: drive sudo with the cached
            # password (via askpass) so we prompt once, not every action — and
            # sudo's own timestamp then skips most re-auths. Without a password
            # yet, ask the UI to collect one. (If the failure isn't about a
            # password, the user likely isn't a sudoer; fall through to pkexec.)
            if sudo_needs_password(check.stderr or ""):
                if self._password is not None:
                    self._escalation = ["sudo", "-A"]
                    return self._escalation
                raise NeedsPassword()
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
        self._sudo_password: str | None = None
        self._remote_uid: int | None = None
        self._sudo_passwordless: bool | None = None  # cached `sudo -n` probe result

    def set_password(self, password: str | None) -> None:
        """Use `password` for login (None reverts to key/agent-only, BatchMode)."""
        self._password = password or None
        self._remote_uid = None  # re-probe identity under the new credentials

    def had_password(self) -> bool:
        """True if a password is already in use (so a fresh failure is a retry)."""
        return self._password is not None

    def set_sudo_password(self, password: str | None) -> None:
        """Cache (or, with None, forget) the *remote sudo* password for this
        session — separate from the SSH login password. Used with ``sudo -S`` so
        a host that requires a password for sudo can be managed without a tty."""
        self._sudo_password = password or None

    def had_sudo_password(self) -> bool:
        return self._sudo_password is not None

    def escalation_status(self) -> str:
        """How root can be reached on the remote host without prompting per
        action: ``"ready"`` (root login, passwordless sudo, or a cached sudo
        password), ``"needs_password"`` (a sudoer who must supply a password), or
        ``"unavailable"`` (not a sudoer / no sudo). Mirrors
        :meth:`LocalRunner.escalation_status` so the UI can prompt once, up front,
        instead of letting a privileged run fail."""
        try:
            if self._remote_is_root():
                return "ready"
        except CommandError:
            return "unavailable"  # can't even probe identity (connection/auth)
        if self._sudo_password is not None or self._sudo_passwordless:
            return "ready"
        try:
            self._run_remote("sudo -n true")
        except CommandError as exc:
            return "needs_password" if sudo_needs_password(exc.stderr) else "unavailable"
        self._sudo_passwordless = True
        return "ready"

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

    def _run_remote(self, remote_command: str, *, timeout: int = READ_TIMEOUT,
                    stdin_text: str | None = None) -> str:
        argv = self._ssh_argv(remote_command)
        # ssh forwards our stdin to the remote command, so `stdin_text` is how a
        # remote `sudo -S` receives its password. With no stdin_text we close
        # stdin (DEVNULL) as before. Login auth never uses stdin — it goes through
        # the SSH_ASKPASS helper — so the two passwords don't collide.
        stdin_kwargs = (
            {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
        )
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._ssh_env(),
                # Detach from any controlling terminal so ssh asks the askpass
                # helper for the password instead of trying to read a tty.
                start_new_session=self._password is not None,
                **stdin_kwargs,
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
        if self._sudo_password is not None:
            # -S reads the password from stdin (no tty needed); -p '' silences the
            # prompt text. The password reaches sudo only through the SSH channel's
            # stdin — never the command line, the remote env, or disk.
            remote = "sudo -S -p '' sh -c " + shlex.quote(script)
            return self._run_remote(
                remote, timeout=WRITE_TIMEOUT, stdin_text=self._sudo_password + "\n"
            )
        # No cached sudo password: try passwordless. -n never prompts (an
        # interactive prompt can't work through ssh), so this needs passwordless
        # sudo or root login; otherwise the UI prompts for a password and retries.
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
