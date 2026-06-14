"""Read network state from a host via iproute2's JSON output."""

from __future__ import annotations

import json

from netgrip.core.model import Address, Interface
from netgrip.core.runner import Runner

PROBE_COMMAND = ["ip", "-details", "-json", "address", "show"]
ROUTE_COMMAND = ["ip", "-json", "route", "show"]
# One round trip for DNS: capability marker on line 1, resolv.conf after it.
DNS_COMMAND = [
    "sh", "-c",
    "command -v resolvectl >/dev/null 2>&1 && echo yes || echo no; "
    "cat /etc/resolv.conf 2>/dev/null",
]

# Routing protocols that mean "the kernel/DHCP/RA put this here", not the user.
_DYNAMIC_PROTOCOLS = {"dhcp", "ra", "redirect", "kernel"}


def probe(runner: Runner) -> list[Interface]:
    out = runner.run(PROBE_COMMAND)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse iproute2 JSON output from '{runner.label}'. "
            "netgrip needs iproute2 4.14 or newer on the managed host."
        ) from exc
    interfaces = parse_addr_json(payload)
    _enrich_gateways(runner, interfaces)
    return interfaces


def probe_dns(runner: Runner) -> tuple[list[str], list[str], bool]:
    """Return (nameservers, search domains, can_edit_dns).

    Best-effort: a host without resolv.conf or one we can't read just yields
    empty lists, never an error — DNS visibility is a convenience.
    """
    try:
        out = runner.run(DNS_COMMAND)
    except (RuntimeError, ValueError):
        return [], [], False
    lines = out.splitlines()
    can_edit = bool(lines) and lines[0].strip() == "yes"
    servers, search = parse_resolv_conf("\n".join(lines[1:]))
    return servers, search, can_edit


def _enrich_gateways(runner: Runner, interfaces: list[Interface]) -> None:
    try:
        routes = json.loads(runner.run(ROUTE_COMMAND))
    except (RuntimeError, ValueError):
        return  # routing info is a bonus; never fail the probe over it
    gateways = parse_route_json(routes)
    for iface in interfaces:
        if iface.name in gateways:
            iface.gateway, iface.gateway_dynamic = gateways[iface.name]


def parse_route_json(payload: list[dict]) -> dict[str, tuple[str, bool]]:
    """Map dev -> (default gateway, is_dynamic) from `ip -json route show`."""
    gateways: dict[str, tuple[str, bool]] = {}
    for route in payload:
        if route.get("dst") != "default":
            continue
        gw, dev = route.get("gateway"), route.get("dev")
        if not gw or not dev or dev in gateways:
            continue
        dynamic = route.get("protocol") in _DYNAMIC_PROTOCOLS
        gateways[dev] = (gw, dynamic)
    return gateways


def parse_resolv_conf(text: str) -> tuple[list[str], list[str]]:
    """Pull nameservers and search domains out of resolv.conf text."""
    servers: list[str] = []
    search: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        parts = line.split()
        if parts[0] == "nameserver" and len(parts) >= 2:
            if parts[1] not in servers:
                servers.append(parts[1])
        elif parts[0] in ("search", "domain"):
            for domain in parts[1:]:
                if domain not in search:
                    search.append(domain)
    return servers, search


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
            alias=item.get("ifalias") or "",
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
