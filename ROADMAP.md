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

Every mutation grows a three-way choice: **Try** (apply to the running config,
auto-reverting host-side after a timeout unless kept — the safety net that keeps
a bad change from locking you out of a remote box), **Apply** (runtime only, as
today) and **Save** (persist through the detected backend). Progress and the
milestone breakdown live in [docs/0.2-TEST-PLAN.md](docs/0.2-TEST-PLAN.md);
backend detection, the persistence indicator and the static pre-fill fix below
have landed.

- **Make the Addressing "Dynamic" toggle actionable.** Today, picking *Dynamic*
  in the IPv4/IPv6 settings dialog (`IpGroupDialog`) is a pure no-op: switching
  a static interface to DHCP/RA does nothing, because `_ipgroup_plan` only ever
  *adds* a static address/gateway/DNS — there's no "tear down static + start a
  client" path (it needs this backend). Two parts: (a) Dynamic should remove the
  existing static address (and clear the static gateway) and start the DHCP/RA
  client; (b) fix the dialog default — for an existing static interface the
  address field currently defaults to *Dynamic* with an empty value and doesn't
  pre-fill the static address, so it can't even show/edit static today and a
  future Dynamic=teardown would risk wiping config on a no-touch OK.

## 0.3 — more of the network

- Bridge creation (same gesture as bonds)
- **Non-default routes** as canvas boxes attached to IP configs (the default
  gateway already landed in 0.1; this adds arbitrary static routes)
- **DNS search domains** editing and richer per-link DNS management
- DHCP client control (request/release) where a persistence backend allows
- `teamd` teams (read support first)

## 0.4 — visibility: containers, virtualization & firewall

This is where NetGrip earns its keep. A Proxmox node or Docker host has dozens
of `veth`s and bridges whose relationships are invisible today: a `veth` shows
as a stray NIC, and a vlan-aware bridge shows no tags, so the canvas is a flat
mesh of unconnected boxes. The job here is *read-only* clarity first — show what
connects to what — before any editing.

This starts with a small model change: generalize the current VLAN-specific
parent/child plumbing (`Interface.vlan_parent` / `vlan_id`, the parent→child
`Edge`) into a shared **"virtual interface on a parent link"** concept that
`veth` peers and bridge-VLAN ports reuse, rather than special-casing each kind.
Note these are three *different* kernel objects, not one to be merged: an 802.1q
**VLAN** subinterface (a tagged child of one parent), a **veth** (a two-ended
virtual cable, usually host-bridge ↔ container), and a **bridge port's VLAN
filtering** (PVID + tagged list on a vlan-aware bridge). They share plumbing and
a "virtual" feel, but each keeps its own correct name — we do *not* rebrand VLAN
to veth.

- **veth pairs as first-class links.** A `veth` is a virtual cable with two
  ends, not a NIC. Pair the two ends (peer ifindex from `ip -d -json link`, no
  privilege needed) and draw them as a single edge, so a container's `veth`
  visibly lands on its host bridge instead of floating free.
- **Docker awareness:** label each `veth` with its container and **docker
  network** name; show which **host ports a container forwards** (`docker
  inspect` / `docker network inspect`; published ports cross-checked against
  `iptables -t nat` / `nft`). Display-first; make alterable only where it
  clearly makes sense.
- **Proxmox / vlan-aware bridges:** read each bridge member port's
  **tagged/untagged VLANs** (`bridge -json vlan show`: `pvid` = untagged,
  `vlanlist` = tagged) and the `bridge-vlan-aware` flag (from
  `/etc/network/interfaces`). Render a member as a port carrying its VLAN tags,
  so a Proxmox node's VLAN topology is legible instead of a flat mesh.
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
