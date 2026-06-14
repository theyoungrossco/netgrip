# Changelog

All notable changes to NetGrip are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

_Nothing yet._

## [0.1.0] - 2026-06-14

First release.

### Added

- Canvas view of the host's network: NICs, bonds, bridges and VLANs as boxes,
  with one box per IPv4/IPv6 address joined to its interface by a line
- Drag an address box between interfaces to move it; Ctrl-drag to clone it
- Draft IP configs: detached address boxes that live on the canvas until
  attached somewhere
- Drag NIC onto NIC to create a bond; all kernel bonding modes including LACP
  (802.3ad) and failover (active-backup); bond mode and membership changes
- VLAN creation, deletion and re-parenting by drag
- Link up/down toggling
- Link **Properties** dialog: edit a NIC/bond/VLAN's MAC, MTU and alias, or
  rename the interface
- **Default gateway** per interface, shown on the box and editable with a
  Dynamic/Static toggle (Dynamic leaves a DHCP-assigned gateway untouched)
- **DNS servers** read from `/etc/resolv.conf` and drawn as a frame around the
  whole diagram (DNS is system-wide); settable per-link via `resolvectl` where
  systemd-resolved is present
- Free-form **names** for IP-config boxes, shown as the box title
- Interface **alias** (kernel `ifalias`) read and shown on the box
- **Light/dark theming** following the OS colour scheme, with a toolbar Theme
  selector (System / Light / Dark) remembered across runs
- Drafts, box positions and box names **persist to disk** (under
  `~/.local/share/netgrip/`), restored per host on the next launch
- Remote host management over SSH, with the host picker pre-filled from
  `~/.ssh/config`
- Confirmation dialog showing the exact iproute2 commands before any change;
  invalid input is reported inline (no stacked dialogs)
- Demo mode (`netgrip --demo`)

[Unreleased]: https://github.com/theyoungrossco/netgrip/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/theyoungrossco/netgrip/releases/tag/v0.1.0
