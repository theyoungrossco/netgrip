# Changelog

All notable changes to NetGrip are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
