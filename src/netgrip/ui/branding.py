"""The application icon, loaded from the bundled monogram SVG.

Set once on the QApplication (so every window and dialog inherits it) and used
for the main window. Loading the SVG bytes and rendering them to a pixmap — vs.
``QIcon(path)`` — means the icon survives a zip/`as_file` install and doesn't
depend on lazy file access later.
"""

from __future__ import annotations

from importlib import resources

from PySide6.QtGui import QIcon, QPixmap

APP_ID = "io.github.theyoungrossco.netgrip"


def app_icon() -> QIcon:
    """The NetGrip monogram.

    Falls back to the freedesktop themed icon (present once desktop integration
    is installed), then a generic network glyph, so a stripped environment still
    shows something rather than nothing.
    """
    try:
        data = (resources.files("netgrip.resources") / f"{APP_ID}.svg").read_bytes()
        pixmap = QPixmap()
        if pixmap.loadFromData(data, "SVG") and not pixmap.isNull():
            return QIcon(pixmap)
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        pass

    themed = QIcon.fromTheme(APP_ID)
    if not themed.isNull():
        return themed
    return QIcon.fromTheme("network-wired")
