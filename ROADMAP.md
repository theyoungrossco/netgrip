# Roadmap

NetGrip's long-term ambition is to be *the* obvious graphical way to manage
Linux network interfaces — solid enough to ship as a standard package in
distributions. This file tracks the path there, roughly in order.

The guiding shape of the work: keep each release independently shippable and
testable, keep the `core` headless and the UI flat, and never apply a change
the user hasn't seen as an exact command first.

## 0.1 — now

- Canvas view of NICs, bonds, bridges, VLANs and IP boxes
- **One address per IP box** (was per-family) so a single address drags to a
  new interface on its own
- Move / clone IP configs by drag; drafts (detached configs)
- **Drafts, box positions and box names persist to disk** and survive restarts
  (`core/store.py`, under `~/.local/share/netgrip/`)
- **Edit link properties** — MAC address, MTU, interface alias and rename — from
  a NIC's Properties dialog
- **Default gateway** per interface (read from `ip route`, set at runtime), with
  a Dynamic/Static toggle so DHCP-assigned values are left alone
- **DNS servers** shown (from `/etc/resolv.conf`); set per-link via `resolvectl`
  where systemd-resolved is present
- **Name an IP-config box** (a free-form label kept in app metadata)
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
change will persist. Persistent renames/aliases (systemd `.link` files, udev)
also land here, since they need this backend.

## 0.3 — more of the network

- Bridge creation (same gesture as bonds)
- **Non-default routes** as canvas boxes attached to IP configs (the default
  gateway already landed in 0.1; this adds arbitrary static routes)
- **DNS search domains** editing and richer per-link DNS management
- DHCP client control (request/release) where a persistence backend allows
- `teamd` teams (read support first)

## 0.4 — visibility: containers & firewall

- **Docker awareness:** label each `veth` with its container and **docker
  network** name; show which **host ports a container forwards**. Display-first;
  make alterable only where it clearly makes sense.
- **Inbound firewall rules** per interface, read from `nft -j list ruleset`
  (fallback `iptables-save`). Display first; editing is a later, careful step.

## 0.5 — scale and polish

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
