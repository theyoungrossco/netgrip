"""Persist link-layer properties (name, alias, MAC, MTU) via systemd ``.link``.

The IP-config half of *Save* lives in :mod:`netgrip.core.persist` and is
backend-specific (NetworkManager / networkd / netplan / ifupdown). Link-layer
properties are different in kind: a rename, an ifalias, a MAC or an MTU are
applied by ``systemd-udevd`` from ``.link`` files, *beneath* whichever subsystem
owns addressing. So one mechanism persists them on every host NetGrip manages —
all of the supported backends run on systemd — instead of four divergent ones.

This is the pure renderer; like every other mutation the actual write goes
through :func:`netgrip.core.actions.plan_write_file`, a plan the user confirms
first. Only the properties the user actually changed are written, so a NetGrip
``.link`` file never pins a value (a MAC, an MTU) the user never touched.

Scope, and the deliberate limitation: NetGrip has *already* made the runtime
change, so Save does not re-trigger udev — renaming a live link needs it down,
and forcing a device re-add on a remote box is exactly the disruption Save must
avoid. The file therefore takes effect on the next boot (the runtime already
matches it). A ``.link`` rule matches by ``OriginalName=`` — the device's
boot-time name, which for a link NetGrip renamed is the *pre-rename* name — so it
re-binds correctly when the device reappears with its kernel-assigned name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netgrip.core.actions import plan_write_file
from netgrip.core.model import Interface

# Property keys, shared with the UI's dirty-tracking so it records *which*
# link-layer properties a gesture changed (only those are rendered).
NAME = "name"
ALIAS = "alias"
MAC = "mac"
MTU = "mtu"

LINK_DIR = "/etc/systemd/network"


@dataclass
class LinkProps:
    """The persistable link-layer properties of one interface, plus the subset
    the user actually changed (``changed``). ``match_name`` is the device's
    boot/original name, written as ``[Match] OriginalName=`` so the rule re-binds
    when the device reappears — for a renamed link that is the *pre-rename* name,
    otherwise just the current name."""

    name: str  # current device name (the rename target)
    match_name: str  # OriginalName= to match on at boot
    alias: str = ""
    mac: str = ""
    mtu: int = 0
    changed: frozenset[str] = field(default_factory=frozenset)

    def renames(self) -> bool:
        return NAME in self.changed and self.name != self.match_name


def link_props(iface: Interface, changed: set[str], match_name: str | None = None) -> LinkProps:
    """Distil a running :class:`Interface` into its link-layer config.

    ``changed`` is the set of property keys the user edited (a subset of
    ``NAME``/``ALIAS``/``MAC``/``MTU``); only those are rendered. ``match_name``
    is the device's boot name to match on — pass the pre-rename name for a renamed
    link, else it defaults to the current name."""
    return LinkProps(
        name=iface.name,
        match_name=match_name or iface.name,
        alias=iface.alias,
        mac=iface.mac,
        mtu=iface.mtu,
        changed=frozenset(changed),
    )


def link_path(name: str) -> str:
    """The ``.link`` drop-in NetGrip owns for ``name``. The ``10-`` prefix orders
    it ahead of distro defaults; the ``netgrip-`` tag makes its author obvious.
    Keyed by the device's *current* name (the rename target, if any)."""
    return f"{LINK_DIR}/10-netgrip-{name}.link"


def link_file(props: LinkProps) -> str:
    """Render a systemd ``.link`` unit for one interface.

    ``[Match] OriginalName=`` binds it to the device by its boot-time name;
    ``[Link]`` carries only the changed properties. An empty ``Alias=`` clears the
    ifalias. A direct ``MACAddress=`` takes effect because no ``MACAddressPolicy=``
    is set (the two are alternatives)."""
    lines = ["[Match]", f"OriginalName={props.match_name}", "", "[Link]"]
    if NAME in props.changed:
        lines.append(f"Name={props.name}")
    if ALIAS in props.changed:
        lines.append(f"Alias={props.alias}")
    if MAC in props.changed:
        lines.append(f"MACAddress={props.mac}")
    if MTU in props.changed:
        lines.append(f"MTUBytes={props.mtu}")
    return "\n".join(lines) + "\n"


def plan_link_files(props_list: list[LinkProps]) -> list[list[str]]:
    """Write a ``.link`` drop-in per changed link, then reload udev's rules.

    ``udevadm control --reload`` is non-disruptive — it reloads the rule/database
    files without re-applying anything to existing devices — so the new ``.link``
    is in place for the next boot while the live link (already changed by the
    runtime Apply) is left untouched. Links with no recorded change contribute
    nothing, so an IP-only Save emits no ``.link`` work."""
    plan: list[list[str]] = []
    for props in props_list:
        if not props.changed:
            continue
        plan += plan_write_file(link_path(props.name), link_file(props))
    if plan:
        plan.append(["udevadm", "control", "--reload"])
    return plan
