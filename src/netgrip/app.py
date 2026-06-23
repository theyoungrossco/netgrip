"""Application entry point."""

from __future__ import annotations

import argparse
import signal
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import netgrip


def _free_windows_console() -> None:
    """Release any console the setuptools GUI launcher may have allocated.

    The launcher stub uses SetConsoleCtrlHandler to relay Ctrl+C, which can
    cause Windows to attach a console to the process.  Calling FreeConsole
    immediately drops it before Windows Terminal renders a tab for it.
    Safe to call when there is no console (returns False, no side-effects).
    """
    import ctypes
    ctypes.windll.kernel32.FreeConsole()


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        _free_windows_console()

    parser = argparse.ArgumentParser(
        prog="netgrip",
        description="Visual, drag-and-drop network interface manager.",
    )
    parser.add_argument(
        "--host", metavar="HOST",
        help="connect to a remote host over ssh on startup (e.g. user@10.0.0.2)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="start with canned demo data; no commands are executed",
    )
    parser.add_argument(
        "--version", action="version", version=f"netgrip {netgrip.__version__}"
    )
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("netgrip")
    app.setOrganizationName("netgrip")
    app.setApplicationDisplayName("NetGrip")
    app.setDesktopFileName("io.github.theyoungrossco.netgrip")

    from netgrip.ui.branding import app_icon
    app.setWindowIcon(app_icon())

    # Match the desktop's light/dark theme (or the user's saved override).
    from netgrip.ui import theme
    pref = QSettings().value("theme", "system")
    theme.apply_theme(app, pref if pref in ("system", "light", "dark") else "system")

    # Let Ctrl-C in the launching terminal close the app (POSIX only;
    # on Windows there is no terminal and the call can allocate a console).
    if sys.platform != "win32":
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    from netgrip.ui.main_window import MainWindow

    window = MainWindow(initial_host=args.host, demo=args.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
