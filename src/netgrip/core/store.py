"""On-disk persistence of UI state that the kernel cannot hold.

Drafts (IP configs not attached anywhere), remembered box positions and the
free-form names a user gives to IP-config boxes outlive the kernel's idea of
the network, so they live here instead: one JSON file per host under the XDG
data directory.

This module is deliberately Qt-free (only ``os``/``pathlib``/``json``) so it
stays in ``core`` and is testable headless, like the rest of the backend.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

APP_DIR = "netgrip"

# A host label may be anything ssh accepts (``user@host``, IPv6 literals, …);
# squeeze it into a safe single filename component.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.@-]+")


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return Path(base) / APP_DIR


def _host_file(label: str) -> Path:
    safe = _UNSAFE.sub("_", label).strip("_") or "host"
    return data_dir() / f"{safe}.json"


def load_host(label: str) -> dict:
    """Return the saved state for ``label``, or a blank skeleton.

    Never raises for a missing or corrupt file: persistence is a convenience,
    not something whose failure should stop the user managing their network.
    """
    path = _host_file(label)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    return {
        "positions": data.get("positions") or {},
        "drafts": data.get("drafts") or [],
        "aliases": data.get("aliases") or {},
    }


def save_host(label: str, data: dict) -> None:
    """Write ``data`` for ``label``, creating the data dir as needed.

    Writes atomically (temp file + replace) so a crash mid-write can't leave a
    truncated file that would lose every draft on the next launch.
    """
    path = _host_file(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _blank() -> dict:
    return {"positions": {}, "drafts": [], "aliases": {}}
