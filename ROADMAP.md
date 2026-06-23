# Roadmap

NetGrip's long-term ambition is to be *the* obvious graphical way to manage
Linux network interfaces — solid enough to ship as a standard package in
distributions. This file tracks the path there, roughly in order — it's a
direction, not a contract, and is subject to change as priorities and what we
learn shift.

The guiding shape of the work: keep each release independently shippable and
testable, keep the `core` headless and the UI flat, and never apply a change
the user hasn't seen as an exact command first.

The app is complex enough now that **each feature add is its own milestone** —
one focused capability per `0.x`, stabilised and merged to `main` with a tagged
release before the next one starts. No more piling several features under one
version.

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
change will persist. Persistent renames/aliases — and MAC/MTU — land here too,
written as systemd `.link` files (udev) beneath whichever backend owns
addressing (`core/persist_link.py`, milestone M6).

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

## 0.4 — Docker visibility (current — stabilising for release)

This is where NetGrip earns its keep, and it's scoped to a **single** feature:
making the Docker container layer legible, *read-only*. It's nearly complete —
the plan is to stabilise it, merge to `main` and cut a **0.4.0** release before
the next milestone starts.

A Docker host has dozens of `veth`s and bridges whose relationships are
invisible today, so the canvas reads as a flat mesh of unconnected boxes. The
job is *read-only* clarity — show what connects to what — before any editing.
Two foundations landed in 0.3 and unblock it: **veth pairs draw as a single
shared cable** (peer matched from `ip -d -json link`) and **vlan-aware bridge
ports show their PVID + tagged lists**.

Surface the container layer the host already half-shows: a `docker0` / `br-…`
bridge is just an unexplained bridge, and a container's `veth` lands on it
anonymously. Read it with `docker network inspect` + `docker inspect`
(best-effort, never fails the probe) and draw it so it *makes sense*:

- each **bridge network** labelled with its docker network name;
- each **container** as its own box on the bridge(s) it joins, showing its
  **IP per network** and its **compose project / service** (so "IPs per
  composed container" is legible at a glance);
- **published ports** as a **dashed, labelled connector** from the container to
  the host's uplink — `:8080→80/tcp` — so it's obvious that *only certain ports
  traverse* from the host into the container, and which host IP they bind.

Later (own follow-up milestones if they grow): cross-check published ports
against `iptables -t nat` / `nft` rather than trusting docker's own view; pin
each individual host `veth` to its container, which needs a netns read; offer
edits where they clearly make sense. See [docs/0.4-PLAN.md](docs/0.4-PLAN.md).

## 0.5 — Proxmox / vlan-aware bridges

Build on the port tags shipped in 0.3 — show the `bridge-vlan-aware` flag's
source (`/etc/network/interfaces`) and tidy how a trunk vs. access port reads,
so a Proxmox node's VLAN topology is fully legible. *Read-only* first, as with
Docker.

The shared-plumbing model note holds throughout: an 802.1q **VLAN**
subinterface, a **veth** virtual cable, and a **bridge port's VLAN filtering**
are three *different* kernel objects that share a "virtual" feel but each keep
their own correct name — we do *not* rebrand one to another.

## 0.6 — inbound firewall rules

Per interface, read from `nft -j list ruleset` (fallback `iptables-save`).
Display first; editing is a later, careful step.

## 0.7 — scale and polish

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
