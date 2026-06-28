"""Application entry point."""

from __future__ import annotations

import argparse
import sys

import netgrip
from netgrip.core.display import choose_gui, has_display
from netgrip.core.model import Interface

# ---------------------------------------------------------------------------
# CLI text renderer (no Qt)
# ---------------------------------------------------------------------------

def render_text(label: str, interfaces: list[Interface]) -> str:
    """Plain-text summary of network interfaces for headless / CLI mode."""
    if not interfaces:
        return "(no interfaces found)"
    lines = [f"netgrip — {label}", ""]
    for iface in interfaces:
        state = "UP  " if iface.is_up else "down"
        mac = f"  {iface.mac}" if iface.mac else ""
        lines.append(f"  {iface.name:<16}{state}  {iface.kind:<12}{mac}")
        if iface.addresses:
            for addr in iface.addresses:
                dyn = " (dynamic)" if addr.dynamic else ""
                lines.append(f"      {addr.cidr}{dyn}")
        else:
            lines.append("      (no addresses)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netgrip",
        description="Visual, drag-and-drop network interface manager.",
    )
    parser.add_argument(
        "--host", metavar="HOST",
        help="connect to a remote host over SSH on startup (e.g. user@10.0.0.2)",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="use canned demo data; no commands are executed",
    )
    parser.add_argument(
        "--version", action="version", version=f"netgrip {netgrip.__version__}",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--gui", action="store_true",
        help="force the GUI even when no display is detected",
    )
    mode.add_argument(
        "--cli", action="store_true",
        help="force plain-text output; skip the GUI even when a display is available",
    )
    return parser


def _launch_gui(args: argparse.Namespace) -> int:
    """Launch the PySide6 GUI. Qt is imported lazily so the CLI path stays Qt-free."""
    import signal

    if sys.platform == "win32":
        _free_windows_console()

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "netgrip: PySide6 is required for the GUI.\n"
            "Install it with:  pip install PySide6",
            file=sys.stderr,
        )
        return 1

    app = QApplication(sys.argv[:1])
    app.setApplicationName("netgrip")
    app.setOrganizationName("netgrip")
    app.setApplicationDisplayName("NetGrip")
    app.setDesktopFileName("io.github.theyoungrossco.netgrip")

    from netgrip.ui.branding import app_icon
    app.setWindowIcon(app_icon())

    from netgrip.ui import theme
    pref = QSettings().value("theme", "system")
    theme.apply_theme(app, pref if pref in ("system", "light", "dark") else "system")

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    from netgrip.ui.main_window import MainWindow
    window = MainWindow(initial_host=args.host, demo=args.demo)
    window.show()
    return app.exec()


def _cli_main(args: argparse.Namespace) -> int:
    """Headless path: probe and print a plain-text interface listing."""
    if args.demo:
        from netgrip.core.demo import demo_interfaces
        label = "demo host (read-only)"
        interfaces = demo_interfaces()
        print(render_text(label, interfaces))
        return 0

    from netgrip.core.runner import CommandError, LocalRunner, SSHRunner

    runner = SSHRunner(args.host) if args.host else LocalRunner()
    try:
        from netgrip.core.probe import probe
        interfaces = probe(runner)
    except CommandError as exc:
        print(f"netgrip: could not read interfaces: {exc}", file=sys.stderr)
        return 1

    print(render_text(runner.label, interfaces))
    return 0


def _free_windows_console() -> None:
    """Release any console the setuptools GUI launcher may have allocated."""
    import ctypes
    ctypes.windll.kernel32.FreeConsole()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if choose_gui(force_gui=args.gui, force_cli=args.cli):
        return _launch_gui(args)

    # Auto-detected headless: let the user know why the GUI didn't open.
    if not args.cli and not has_display():
        print(
            "netgrip: no display detected (DISPLAY/WAYLAND_DISPLAY not set).\n"
            "Running in CLI mode. Pass --gui to force the GUI.",
            file=sys.stderr,
        )

    return _cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
