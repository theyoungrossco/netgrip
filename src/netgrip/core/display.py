"""Display availability detection — pure Python, no Qt.

Checked before attempting to start PySide6 so the headless core path never
tries to open a display.
"""

from __future__ import annotations

import os


def has_display() -> bool:
    """Return True when an X11 or Wayland display appears to be available.

    Reads DISPLAY (X11) and WAYLAND_DISPLAY (Wayland) only — no Qt import.
    Safe to call from the headless core and from test environments that set
    QT_QPA_PLATFORM=offscreen without a real display.
    """
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def choose_gui(*, force_gui: bool = False, force_cli: bool = False) -> bool:
    """Return True if we should launch the GUI.

    Explicit flags win; when neither is given, auto-detect from the environment.
    """
    if force_gui:
        return True
    if force_cli:
        return False
    return has_display()
