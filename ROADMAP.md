# Roadmap

NetGrip's long-term ambition is to be *the* obvious graphical way to manage
Linux network interfaces — solid enough to ship as a standard package in
distributions. This file tracks the path there, roughly in order.

## 0.1 — now

- Canvas view of NICs, bonds, bridges, VLANs and per-family IP boxes
- Move / clone IP configs by drag, drafts (detached configs)
- Create bonds by dragging NICs together; modes incl. LACP and failover
- VLAN create/delete/re-parent
- Remote hosts over SSH (using `~/.ssh/config` hosts)
- Demo mode
- Every change confirmed as an explicit iproute2 command plan

## 0.2 — persistence (the big one)

Runtime-only changes are honest but not enough. Plan: a backend abstraction
that detects what owns the host's network config and writes through it:

- NetworkManager (D-Bus / `nmcli`) — most desktops
- systemd-networkd — most modern servers
- netplan — Ubuntu server
- "runtime only" stays available as the fallback, clearly labelled

The UI gains a per-host indicator of which backend is in use and whether a
change will persist.

## 0.3 — more of the network

- Bridge creation (same gesture as bonds)
- Routes and default gateways as canvas boxes attached to IP configs
- DHCP client control (request/release) where a persistence backend allows
- MTU and link property editing
- `teamd` teams (read support first)

## 0.4 — scale and polish

- Multiple hosts open at once (tabs), copy a config box *between hosts*
- Canvas search/filter for machines with many interfaces
- Saved "profiles": a set of draft boxes you can apply as a unit
- Accessibility and keyboard-only operation
- Translations

## Distribution packaging

- Debian/Ubuntu packaging (`debian/`), aiming for inclusion in Debian —
  see [docs/PACKAGING.md](docs/PACKAGING.md)
- Fedora/openSUSE specs, AUR
- Flatpak (tricky: needs host network access; investigate portal story)

## Someday / maybe

- Windows hosts (the core/UI split and runner abstraction were designed so a
  WinRM/PowerShell runner + netsh/NetAdapter backend can slot in)
- A polkit-authenticated helper daemon so unprivileged sessions get
  fine-grained authorization instead of blanket sudo
- Read-only "observer" mode for NOC wall displays
