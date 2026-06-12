"""Application entry point."""

from __future__ import annotations

import argparse
import signal
import sys

from PySide6.QtWidgets import QApplication

import netgrip


def main(argv: list[str] | None = None) -> int:
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
    app.setApplicationDisplayName("NetGrip")
    app.setDesktopFileName("io.github.theyoungrossco.netgrip")

    # Let Ctrl-C in the launching terminal close the app.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    from netgrip.ui.main_window import MainWindow

    window = MainWindow(initial_host=args.host, demo=args.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
