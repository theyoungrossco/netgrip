"""Collect host aliases from the user's OpenSSH client configuration."""

from __future__ import annotations

import glob
import os

_MAX_INCLUDE_DEPTH = 4


def ssh_config_hosts(path: str | None = None) -> list[str]:
    """Return concrete Host aliases from ~/.ssh/config (and its Includes).

    Pattern entries containing wildcards or negations are skipped, since they
    do not name a connectable host.
    """
    path = path or os.path.expanduser("~/.ssh/config")
    found: list[str] = []
    _parse_file(path, found, depth=0)
    return sorted(set(found), key=str.lower)


def _parse_file(path: str, found: list[str], depth: int) -> None:
    if depth >= _MAX_INCLUDE_DEPTH or not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return

    base_dir = os.path.dirname(path)
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Both "Key value" and "Key=value" are valid ssh_config syntax.
        normalized = line.replace("=", " ", 1) if "=" in line.split()[0] else line
        parts = normalized.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].lower(), parts[1].strip()

        if key == "host":
            for token in value.split():
                if not any(ch in token for ch in "*?!"):
                    found.append(token)
        elif key == "include":
            for pattern in value.split():
                pattern = os.path.expanduser(pattern)
                if not os.path.isabs(pattern):
                    pattern = os.path.join(base_dir, pattern)
                for included in sorted(glob.glob(pattern)):
                    _parse_file(included, found, depth + 1)
