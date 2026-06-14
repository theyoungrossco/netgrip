# Changelog

All notable changes to NetGrip are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- One IP box per address (was one box per family), so a single address can be
  dragged, cloned or detached on its own
- Link **Properties** dialog: edit a NIC/bond/VLAN's MAC, MTU and alias, or
  rename the interface
- **Default gateway** per interface, shown on the box and editable with a
  Dynamic/Static toggle (Dynamic leaves a DHCP-assigned gateway untouched)
- **DNS servers** read from `/etc/resolv.conf` and shown in a system DNS box;
  settable per-link via `resolvectl` where systemd-resolved is present
- Free-form **names** for IP-config boxes, shown as the box title
- Interface **alias** (kernel `ifalias`) is read and shown on the box
- Drafts, box positions and box names **persist to disk** (under
  `~/.local/share/netgrip/`) and are restored on the next launch
- `AGENTS.md` contributor/agent guide (imported by `CLAUDE.md`)

### Changed

- Dialogs report invalid input **inline** instead of opening a second dialog;
  no stacked modal popups anywhere (project rule)

## [0.1.0] - 2026-06-12

Initial release.

### Added

- Node-graph canvas: NICs, bonds, bridges and VLANs as boxes; IPv4 and IPv6
  configurations as separate boxes joined by lines
- Drag an IP config between interfaces to move it; Ctrl-drag to clone
- Draft IP configs: detached boxes that live on the canvas until attached
- Drag NIC onto NIC to create a bond; all kernel bonding modes including
  LACP (802.3ad) and failover (active-backup)
- VLAN creation, deletion and re-parenting by drag
- Link up/down, bond membership and mode changes via context menus
- Remote host management over SSH; host picker pre-filled from
  `~/.ssh/config`
- Confirmation dialog showing the exact iproute2 commands before any change
- Demo mode (`netgrip --demo`)

[Unreleased]: https://github.com/theyoungrossco/netgrip/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/theyoungrossco/netgrip/releases/tag/v0.1.0
