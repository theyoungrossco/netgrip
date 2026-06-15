# Changelog

All notable changes to NetGrip are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Addresses are now grouped, per protocol, into an **IPv4 / IPv6 box** that
  carries that family's gateway, DNS servers and search domains in a clickable
  header. The individual address boxes sit inside it and still drag, clone and
  detach independently; dropping an address into a group attaches it to that
  interface. Right-click the header to edit the family's gateway and DNS.
- **Default gateway and DNS are per-family**, not per-interface: an interface
  can show both an IPv4 and an IPv6 default at once, and a v4 change no longer
  disturbs the v6 default (and vice versa). Gateway/DNS moved off the link
  **Properties** dialog into the IPv4/IPv6 group settings.
- System-wide DNS is now a **System DNS box** at the top instead of a frame
  enclosing the whole diagram (which intercepted clicks meant for the canvas).
  It lists each effective resolver with **where it comes from** (the interface
  that supplied it, "manual", or "system") and takes manually added resolvers.
- A **DHCP/RA-assigned address now sits in its IPv4/IPv6 group header** (the
  "global" section, alongside the lease's gateway and DNS); only **static**
  addresses are drawn as their own boxes inside the frame. Those boxes are now
  titled **"v4 address" / "v6 address"** so the per-address box reads
  differently from the protocol group it sits in.
- Dragging an address **out of its group no longer stretches the frame** to
  follow it: the frame stays put, so leaving it reads as a detach. Dropping the
  box on another group's **title bar** attaches it there; dropping it clear of
  every group detaches it to a draft.

### Added

- Per-link DNS read via `resolvectl` for resolver provenance, bucketed into the
  IPv4/IPv6 group it belongs to.
- Manually added host-wide resolvers, persisted per host and shown in the
  System DNS box.
- **Addressing** Dynamic/Static selector in the IPv4/IPv6 group settings: Static
  adds a fixed address; obtaining one via DHCP/RA is flagged as the 0.2 backend.
- **Draft VLANs**: right-click the canvas to create a VLAN that does not exist
  yet, give it an id, a name and addresses, then drag it onto a parent NIC or
  bond (or use its menu) to create it — addresses and all — in one batch. A free
  IP draft can be folded into a draft VLAN's pending addresses. Draft VLANs
  persist per host like other drafts.

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
