"""Qt-free CLI subcommands for netgrip.

Each cmd_* function maps to one CLI subcommand and returns an integer exit code.
"""

from __future__ import annotations

import ipaddress
import json
import shlex
import sys

from netgrip.core.actions import (
    plan_add_addresses,
    plan_remove_addresses,
    plan_set_link,
    plan_set_mac,
    plan_set_mtu,
    valid_mac,
)
from netgrip.core.backends import detect_backend
from netgrip.core.demo import demo_interfaces
from netgrip.core.runner import CommandError


def _valid_cidr(cidr: str) -> bool:
    try:
        ipaddress.ip_interface(cidr)
        return True
    except ValueError:
        return False


def _iface_to_dict(iface) -> dict:
    gw = {}
    for family, gateway in iface.gateways.items():
        gw[str(family)] = gateway.address
    return {
        "name": iface.name,
        "state": iface.state,
        "kind": iface.kind,
        "mac": iface.mac,
        "addresses": [{"cidr": a.cidr, "dynamic": a.dynamic} for a in iface.addresses],
        "gateways": gw,
    }


def _print_ifaces_text(interfaces, label: str) -> None:
    print(label)
    print()
    for iface in interfaces:
        mac_part = f"  {iface.mac}" if iface.mac else ""
        print(f"  {iface.name:<16} {iface.state.upper():<5} {iface.kind}{mac_part}")
        for addr in iface.addresses:
            dyn = " (dynamic)" if addr.dynamic else ""
            print(f"      {addr.cidr}{dyn}")


def cmd_show(runner, args) -> int:
    try:
        if getattr(args, "demo", False):
            interfaces = demo_interfaces()
            label = "demo"
        else:
            from netgrip.core.probe import probe
            interfaces = probe(runner)
            label = getattr(runner, "label", "localhost")
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps([_iface_to_dict(i) for i in interfaces], indent=2))
    else:
        _print_ifaces_text(interfaces, label)
    return 0


def cmd_backend(runner, args) -> int:
    backend = detect_backend(runner)
    if getattr(args, "json", False):
        print(json.dumps({
            "kind": backend.kind,
            "label": backend.label,
            "summary": backend.summary,
            "persists": backend.persists,
        }))
    else:
        print(f"{backend.label}")
        if backend.summary:
            print(f"  {backend.summary}")
        print(f"  Persists across reboot: {'yes' if backend.persists else 'no'}")
    return 0


def _build_plan(args) -> tuple[str, list[list[str]]] | tuple[None, None]:
    """Return (op_name, plan) or (None, None) if the args are invalid."""
    op = args.op

    if op == "up":
        return "up", plan_set_link(args.iface, True)
    if op == "down":
        return "down", plan_set_link(args.iface, False)

    if op == "set-mtu":
        try:
            mtu = int(args.mtu)
            if mtu <= 0:
                raise ValueError
        except (ValueError, AttributeError):
            print(f"error: invalid MTU: {args.mtu!r}", file=sys.stderr)
            return None, None
        return "set-mtu", plan_set_mtu(args.iface, mtu)

    if op == "set-mac":
        if not valid_mac(args.mac):
            print(f"error: invalid MAC address: {args.mac!r}", file=sys.stderr)
            return None, None
        return "set-mac", plan_set_mac(args.iface, args.mac)

    if op == "add-addr":
        if not _valid_cidr(args.cidr):
            print(f"error: invalid CIDR: {args.cidr!r}", file=sys.stderr)
            return None, None
        return "add-addr", plan_add_addresses(args.iface, [args.cidr])

    if op == "del-addr":
        if not _valid_cidr(args.cidr):
            print(f"error: invalid CIDR: {args.cidr!r}", file=sys.stderr)
            return None, None
        return "del-addr", plan_remove_addresses(args.iface, [args.cidr])

    print(f"error: unknown op: {op!r}", file=sys.stderr)
    return None, None


def cmd_plan(args) -> int:
    op, plan = _build_plan(args)
    if plan is None:
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"op": op, "commands": plan}))
    else:
        for argv in plan:
            print(shlex.join(argv))
    return 0


def cmd_apply(runner, args) -> int:
    op, plan = _build_plan(args)
    if plan is None:
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"op": op, "commands": plan}))
    else:
        for argv in plan:
            print(shlex.join(argv))

    if not getattr(args, "confirm", False):
        print("Re-run with --confirm to apply.")
        return 0

    try:
        runner.run_privileged(plan)
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
