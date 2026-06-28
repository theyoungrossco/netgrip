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

def _add_plan_ops(subparsers: argparse._SubParsersAction, is_apply: bool) -> None:
    """Register plan/apply sub-operations (shared between 'plan' and 'apply')."""
    confirm_flag = {"--confirm": dict(action="store_true",
                                     help="actually run the commands")} if is_apply else {}

    def _sub(name, help_text, *positionals):
        p = subparsers.add_parser(name, help=help_text)
        for pos in positionals:
            p.add_argument(pos)
        for flag, kwargs in confirm_flag.items():
            p.add_argument(flag, **kwargs)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.set_defaults(op=name)

    _sub("up",       "bring an interface up",               "iface")
    _sub("down",     "bring an interface down",             "iface")
    _sub("set-mtu",  "set interface MTU",                   "iface", "mtu")
    _sub("set-mac",  "set interface MAC address",           "iface", "mac")
    _sub("add-addr", "add an IP address (CIDR) to an interface", "iface", "cidr")
    _sub("del-addr", "remove an IP address from an interface",   "iface", "cidr")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netgrip",
        description="Visual, drag-and-drop network interface manager.",
    )
    parser.add_argument(
        "--host", metavar="HOST",
        help="connect to a remote host over SSH (e.g. user@10.0.0.2)",
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

    subs = parser.add_subparsers(dest="subcommand", metavar="COMMAND")

    # netgrip show
    show_p = subs.add_parser("show", help="display live network interface state")
    show_p.add_argument("--json", action="store_true", help="machine-readable output")
    show_p.add_argument("--demo", action="store_true", help="use canned demo data")

    # netgrip backend
    backend_p = subs.add_parser("backend", help="show which config backend owns this host")
    backend_p.add_argument("--json", action="store_true", help="machine-readable output")

    # netgrip plan <op> <args>
    plan_p = subs.add_parser("plan", help="print the iproute2 commands for a change (dry-run)")
    plan_ops = plan_p.add_subparsers(dest="op", metavar="OP")
    _add_plan_ops(plan_ops, is_apply=False)

    # netgrip apply <op> <args>
    apply_p = subs.add_parser(
        "apply",
        help="print commands for a change and run them with --confirm",
    )
    apply_ops = apply_p.add_subparsers(dest="op", metavar="OP")
    _add_plan_ops(apply_ops, is_apply=True)

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

    if args.subcommand in ("show", "backend", "plan", "apply"):
        return _dispatch_subcommand(args)

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


def _dispatch_subcommand(args: argparse.Namespace) -> int:
    from netgrip.cli import cmd_apply, cmd_backend, cmd_plan, cmd_show
    from netgrip.core.runner import LocalRunner, SSHRunner

    runner = SSHRunner(args.host) if getattr(args, "host", None) else LocalRunner()

    if args.subcommand == "show":
        return cmd_show(runner, args)
    if args.subcommand == "backend":
        return cmd_backend(runner, args)
    if args.subcommand == "plan":
        if not getattr(args, "op", None):
            print("usage: netgrip plan <op> <args>  (try: netgrip plan --help)", file=sys.stderr)
            return 1
        return cmd_plan(args)
    if args.subcommand == "apply":
        if not getattr(args, "op", None):
            print("usage: netgrip apply <op> <args>  (try: netgrip apply --help)", file=sys.stderr)
            return 1
        return cmd_apply(runner, args)


if __name__ == "__main__":
    sys.exit(main())
