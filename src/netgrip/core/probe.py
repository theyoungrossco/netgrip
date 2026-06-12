"""Read network state from a host via iproute2's JSON output."""

from __future__ import annotations

import json

from netgrip.core.model import Address, Interface
from netgrip.core.runner import Runner

PROBE_COMMAND = ["ip", "-details", "-json", "address", "show"]


def probe(runner: Runner) -> list[Interface]:
    out = runner.run(PROBE_COMMAND)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse iproute2 JSON output from '{runner.label}'. "
            "netgrip needs iproute2 4.14 or newer on the managed host."
        ) from exc
    return parse_addr_json(payload)


def parse_addr_json(payload: list[dict]) -> list[Interface]:
    """Turn `ip -details -json address show` output into model objects."""
    interfaces: list[Interface] = []
    for item in payload:
        linkinfo = item.get("linkinfo") or {}
        info_data = linkinfo.get("info_data") or {}
        kind = linkinfo.get("info_kind")
        if item.get("link_type") == "loopback":
            kind = "loopback"
        elif not kind:
            kind = "physical"

        iface = Interface(
            name=item.get("ifname", "?"),
            index=item.get("ifindex", 0),
            kind=kind,
            state=_operstate(item),
            mac=item.get("address") or "",
            mtu=item.get("mtu", 0),
            master=item.get("master"),
            vlan_id=info_data.get("id") if kind == "vlan" else None,
            vlan_parent=item.get("link") if kind == "vlan" else None,
            bond_mode=info_data.get("mode") if kind == "bond" else None,
        )

        for ai in item.get("addr_info", []):
            family = 4 if ai.get("family") == "inet" else 6
            scope = ai.get("scope", "global")
            local = ai.get("local")
            if not local:
                continue
            if family == 6 and scope == "link":
                continue  # fe80:: link-locals exist on every up interface; pure noise
            iface.addresses.append(
                Address(
                    address=local,
                    prefixlen=ai.get("prefixlen", 32 if family == 4 else 128),
                    family=family,
                    scope=scope,
                    dynamic=bool(ai.get("dynamic")),
                )
            )
        interfaces.append(iface)
    return interfaces


def _operstate(item: dict) -> str:
    state = (item.get("operstate") or "").lower()
    if state == "unknown":
        # Loopback and some virtual devices report UNKNOWN; fall back to flags.
        return "up" if "UP" in (item.get("flags") or []) else "down"
    return state if state in ("up", "down") else "down"
