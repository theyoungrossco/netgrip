"""Build iproute2 command plans for configuration changes.

Every function returns a list of argv lists ("a plan") without executing
anything. The UI shows the plan to the user for confirmation, then hands it
to :meth:`Runner.run_privileged`, which executes it as one batch.
"""

from __future__ import annotations

import ipaddress
import re
import shlex

from netgrip.core.model import Interface

# A "Try" applies a change to the running config but arms an automatic revert on
# the *host* after a timeout, unless the user keeps it. The reverter runs
# host-side and fully detached, so it still fires if the client process dies or
# the SSH connection drops mid-decision — the safety net that stops a bad change
# (wrong gateway, an address moved off your own uplink) from locking you out of
# a remote box. The client normally reverts at its own shorter countdown; this
# host timer is the backup (the UI sets it a little longer so the client wins
# the race in the normal case). State is a single sentinel file per attempt.
TRY_STATE_DIR = "/tmp/netgrip-try"

# Kernel bonding modes, keyed by the value `ip link` expects.
BOND_MODES = {
    "active-backup": "Failover (active-backup)",
    "802.3ad": "LACP (802.3ad)",
    "balance-rr": "Round-robin (balance-rr)",
    "balance-xor": "XOR hash (balance-xor)",
    "broadcast": "Broadcast",
    "balance-tlb": "Adaptive TX (balance-tlb)",
    "balance-alb": "Adaptive TX+RX (balance-alb)",
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")  # IFNAMSIZ is 16 incl. NUL
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def valid_link_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and name not in (".", "..")


def valid_mac(mac: str) -> bool:
    """A unicast, locally-administrable MAC in xx:xx:xx:xx:xx:xx form.

    Rejects multicast addresses (low bit of the first octet set), which the
    kernel will not accept as a device address anyway.
    """
    if not _MAC_RE.match(mac):
        return False
    return int(mac.split(":", 1)[0], 16) & 1 == 0


def valid_ipaddr(addr: str) -> bool:
    """A bare IPv4 or IPv6 address (no prefix), e.g. a gateway or nameserver."""
    try:
        ipaddress.ip_address(addr)
        return True
    except ValueError:
        return False


def default_vlan_name(parent: str, vlan_id: int) -> str:
    return f"{parent}.{vlan_id}"


def next_bond_name(existing: set[str]) -> str:
    n = 0
    while f"bond{n}" in existing:
        n += 1
    return f"bond{n}"


def plan_add_addresses(dev: str, cidrs: list[str]) -> list[list[str]]:
    return [["ip", "address", "add", cidr, "dev", dev] for cidr in cidrs]


def plan_remove_addresses(dev: str, cidrs: list[str]) -> list[list[str]]:
    return [["ip", "address", "del", cidr, "dev", dev] for cidr in cidrs]


def plan_restore_addresses(dev: str, cidrs: list[str]) -> list[list[str]]:
    """Ensure addresses are present — for *reverting* a removal.

    Uses ``ip address replace`` (add-or-update), so it never fails if the
    address is already there. That matters because a DHCP/RA client can re-add a
    lease we removed during a Try; a plain ``add`` would then abort the revert
    with "Address already assigned", which is exactly the wrong moment to error."""
    return [["ip", "address", "replace", cidr, "dev", dev] for cidr in cidrs]


def plan_move_addresses(src: str, dst: str, cidrs: list[str]) -> list[list[str]]:
    return plan_remove_addresses(src, cidrs) + plan_add_addresses(dst, cidrs)


def plan_set_link(dev: str, up: bool) -> list[list[str]]:
    return [["ip", "link", "set", "dev", dev, "up" if up else "down"]]


def plan_set_mac(dev: str, mac: str) -> list[list[str]]:
    return [["ip", "link", "set", "dev", dev, "address", mac]]


def plan_set_mtu(dev: str, mtu: int) -> list[list[str]]:
    return [["ip", "link", "set", "dev", dev, "mtu", str(mtu)]]


def plan_set_alias(dev: str, alias: str) -> list[list[str]]:
    """Set (or, with an empty string, clear) the kernel ifalias label."""
    return [["ip", "link", "set", "dev", dev, "alias", alias]]


def plan_rename_link(dev: str, new_name: str, was_up: bool) -> list[list[str]]:
    """Rename a link. The kernel only renames a device while it is down."""
    plan = [
        ["ip", "link", "set", "dev", dev, "down"],
        ["ip", "link", "set", "dev", dev, "name", new_name],
    ]
    if was_up:
        plan.append(["ip", "link", "set", "dev", new_name, "up"])
    return plan


def _family_flag(family: int) -> str:
    return "-4" if family == 4 else "-6"


def plan_set_gateway(dev: str, gateway: str, family: int) -> list[list[str]]:
    """Point this family's default route at ``gateway`` via ``dev``.

    `replace` adds the default route or updates it in place, so this works
    whether or not a default route already exists. The family flag keeps an
    IPv4 change from touching the IPv6 default and vice versa.
    """
    return [["ip", _family_flag(family), "route", "replace",
             "default", "via", gateway, "dev", dev]]


def plan_clear_gateway(dev: str, family: int) -> list[list[str]]:
    return [["ip", _family_flag(family), "route", "del", "default", "dev", dev]]


def plan_set_dns(dev: str, servers: list[str], search: list[str]) -> list[list[str]]:
    """Set per-link DNS via systemd-resolved (resolvectl).

    Runtime only and present only where systemd-resolved is; the UI offers this
    just when ``HostState.can_edit_dns`` is true. Reboot-persistent, backend-
    aware DNS is the 0.2 roadmap item.
    """
    plan = [["resolvectl", "dns", dev, *servers]]
    if search:
        plan.append(["resolvectl", "domain", dev, *search])
    return plan


def plan_create_vlan(parent: str, vlan_id: int, name: str | None = None) -> list[list[str]]:
    name = name or default_vlan_name(parent, vlan_id)
    return [
        ["ip", "link", "add", "link", parent, "name", name, "type", "vlan", "id", str(vlan_id)],
        ["ip", "link", "set", "dev", name, "up"],
    ]


def plan_delete_link(name: str) -> list[list[str]]:
    return [["ip", "link", "del", "dev", name]]


def plan_create_bond(name: str, mode: str, members: list[str]) -> list[list[str]]:
    plan = [["ip", "link", "add", name, "type", "bond", "mode", mode]]
    for member in members:
        # A link must be down before it can be enslaved.
        plan.append(["ip", "link", "set", "dev", member, "down"])
        plan.append(["ip", "link", "set", "dev", member, "master", name])
    plan.append(["ip", "link", "set", "dev", name, "up"])
    return plan


def plan_add_member(group: str, dev: str) -> list[list[str]]:
    return [
        ["ip", "link", "set", "dev", dev, "down"],
        ["ip", "link", "set", "dev", dev, "master", group],
        ["ip", "link", "set", "dev", dev, "up"],
    ]


def plan_remove_member(dev: str) -> list[list[str]]:
    return [
        ["ip", "link", "set", "dev", dev, "nomaster"],
        ["ip", "link", "set", "dev", dev, "up"],
    ]


def plan_set_bond_mode(bond: str, mode: str) -> list[list[str]]:
    # The kernel only allows a mode change while the bond is down.
    return [
        ["ip", "link", "set", "dev", bond, "down"],
        ["ip", "link", "set", "dev", bond, "type", "bond", "mode", mode],
        ["ip", "link", "set", "dev", bond, "up"],
    ]


def _try_sentinel(token: str) -> str:
    return f"{TRY_STATE_DIR}/{token}"


def _join_plan(plan: list[list[str]]) -> str:
    """Join a plan into one ``&&``-chained shell fragment (no quoting surprises:
    every argv is shlex-quoted), for the *forward* part of the Try wrapper —
    fail-fast, so a failing step aborts the rest (and never arms the reverter)."""
    return " && ".join(shlex.join(argv) for argv in plan)


def _join_revert(plan: list[list[str]]) -> str:
    """Join a *revert* plan with ``;`` rather than ``&&``: recovery is
    best-effort, so one benign step (e.g. deleting an address the kernel already
    dropped) must not stop the remaining steps from restoring the rest."""
    return "; ".join(shlex.join(argv) for argv in plan)


def plan_try(forward: list[list[str]], revert: list[list[str]], token: str,
             *, timeout: int = 70) -> list[list[str]]:
    """Apply ``forward`` now, then arm a detached host-side revert.

    Returns a one-command plan (so it still confirms and runs as a single batch).
    It creates a sentinel file for ``token``, runs ``forward``, then launches a
    ``setsid`` background job that sleeps ``timeout`` seconds and reverts *only
    if the sentinel still exists*, then clears it. Keeping the change is just
    removing the sentinel (:func:`plan_keep`); reverting early removes it and
    runs the revert at once (:func:`plan_revert_now`). Because the reverter is
    detached (own session, stdio to /dev/null) it survives the SSH channel
    closing, which is the whole point — a lost connection still rolls back."""
    sentinel = shlex.quote(_try_sentinel(token))
    reverter = (
        f"sleep {int(timeout)}; "
        f"if [ -e {sentinel} ]; then {_join_revert(revert)}; fi; "
        f"rm -f {sentinel}"
    )
    script = (
        f"mkdir -p {shlex.quote(TRY_STATE_DIR)} && touch {sentinel} && "
        f"{{ {_join_plan(forward)}; }} && "
        f"setsid sh -c {shlex.quote(reverter)} </dev/null >/dev/null 2>&1 &"
    )
    return [["sh", "-c", script]]


def plan_keep(token: str) -> list[list[str]]:
    """Keep a tried change: drop the sentinel so the armed revert no-ops."""
    return [["sh", "-c", f"rm -f {shlex.quote(_try_sentinel(token))}"]]


def plan_revert_now(token: str, revert: list[list[str]]) -> list[list[str]]:
    """Revert a tried change immediately and disarm its host-side timer."""
    sentinel = shlex.quote(_try_sentinel(token))
    return [["sh", "-c", f"rm -f {sentinel}; {_join_revert(revert)}"]]


def plan_move_vlan(vlan: Interface, new_parent: str) -> list[list[str]]:
    """Re-parent a VLAN by recreating it; the kernel cannot move one in place.

    Keeps the VLAN id and re-applies its addresses. If the VLAN used the
    conventional `<parent>.<id>` name it is renamed to match the new parent.
    """
    name = vlan.name
    if vlan.vlan_parent and name == default_vlan_name(vlan.vlan_parent, vlan.vlan_id or 0):
        name = default_vlan_name(new_parent, vlan.vlan_id or 0)
    plan = plan_delete_link(vlan.name)
    plan += [
        ["ip", "link", "add", "link", new_parent, "name", name,
         "type", "vlan", "id", str(vlan.vlan_id)],
    ]
    plan += plan_add_addresses(name, [a.cidr for a in vlan.addresses])
    if vlan.is_up:
        plan += plan_set_link(name, True)
    return plan
