#!/usr/bin/env python3
"""Render the demo host to PNG screenshots for the README, light and dark.

Uses ``QWidget.grab()`` so the window is painted offscreen — no compositor or
visible display needed beyond a Qt platform plugin. Run from the repo root:

    .venv/bin/python scripts/grab_screenshots.py
"""

from __future__ import annotations

import pathlib
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "img"


def grab(mode: str, path: pathlib.Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("netgrip")
    app.setOrganizationName("netgrip")

    from netgrip.ui import theme
    theme.apply_theme(app, mode)

    from netgrip.ui.main_window import MainWindow

    win = MainWindow(demo=True)
    win.resize(1100, 720)
    win.show()
    app.processEvents()
    win.canvas.fit_all()
    app.processEvents()

    pix = win.grab()
    pix.save(str(path))
    win.close()
    print(f"wrote {path} ({pix.width()}x{pix.height()})")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # Each theme needs its own QApplication so the palette is applied cleanly.
    for mode, name in (("light", "screenshot-demo.png"), ("dark", "screenshot-demo-dark.png")):
        QSettings().setValue("theme", mode)
        grab(mode, OUT / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
