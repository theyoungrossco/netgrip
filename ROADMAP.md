# Roadmap

NetGrip's long-term ambition is to be *the* obvious way to manage Linux network
interfaces — graphical when there's a screen, scriptable when there isn't —
solid enough to ship as a standard package in distributions. This file tracks
the path there, roughly in order — it's a direction, not a contract, and is
subject to change as priorities and what we learn shift.

The guiding shape of the work: keep each release independently shippable and
testable, keep the `core` headless and the UI flat, and never apply a change
the user hasn't seen as an exact command first.

The app is complex enough now that **each feature add is its own milestone** —
one focused capability per `0.x`, stabilised and merged to `main` with a tagged
release before the next one starts. No more piling several features under one
version.

## Shipped

- **0.1 — the canvas.** NICs, bonds, bridges, VLANs and IP boxes; one address
  per box (drag a single address to a new interface); move/clone configs by
  drag; drafts, box positions and names persist to disk (`core/store.py`); edit
  MAC / MTU / alias / rename; per-interface default gateway with Dynamic/Static;
  DNS shown (`/etc/resolv.conf`) and set per-link via `resolvectl`; bonds
  (LACP/failover); VLAN create/delete/re-parent; remote hosts over SSH; demo
  mode; every change confirmed as an exact iproute2 plan.
- **0.2 — persistence.** A backend abstraction that detects who owns the host's
  config (NetworkManager / systemd-networkd / netplan / ifupdown, with
  runtime-only as a clearly-labelled fallback) and writes through it, a per-host
  backend + persistence indicator, and a **Try / Apply / Save** three-way on
  every mutation (Try auto-reverts after a timeout so a bad change can't lock you
  out of a remote box). Persistent link properties via systemd `.link` files.
- **0.3 — more of the network.** Bridge creation; static (non-default) routes;
  DNS search domains; veth pairs drawn as a single shared cable; vlan-aware
  bridge ports showing PVID + tagged lists.
- **0.4 — Docker visibility (read-only).** Bridge networks labelled by docker
  name; each container a box on the networks it joins with per-network IP +
  compose project/service; published ports as dashed labelled connectors
  (`:8080→80/tcp`) to the host IP they bind; container egress as a dotted line.
  (0.4.1: remote sudo-over-SSH, ifupdown2 enablement, SVG/PDF diagram export.
  0.4.2: Windows app-icon packaging fix.)
- **0.5 — WireGuard visibility + live throughput.** WireGuard interfaces
  recognised (link kind `wireguard`) and drawn with their own glyph; live RX/TX
  byte counters on every interface box (`ip -s -j link show`); host-network
  containers (`network_mode: host`) shown linking to the host's uplink; KVM/QEMU
  tap ports labelled "vm tap"; empty docker bridges no longer float as islands.

> The originally-planned 0.5 (Proxmox / vlan-aware bridges) slipped: WireGuard +
> throughput matured on the experimental branch and shipped first. Proxmox is
> now 0.6.

## Next

### 0.6 — WireGuard, in depth

0.5 draws the `wg0` interface but nothing *inside* it. WireGuard is
routing-driven, not interface-bound — encrypted traffic is plain UDP to each
peer's endpoint, routed out whatever interface serves that route (usually the
default), which is exactly why there's no fixed underlying NIC to show. So show
the honest structure instead:

- **peers, endpoints and AllowedIPs**, read from `wg show <dev> dump` (needs
  `CAP_NET_ADMIN`; degrade silently to interface-only when unprivileged), drawn
  as the *stable* topology;
- **current egress**, derived per peer with `ip route get <endpoint>` and drawn
  as a **dashed, "via (current)"** link to that NIC — explicitly marked volatile
  so it reads as roaming, never as a fixed binding;
- **latest handshake** and per-peer transfer as dim annotations, reusing the
  0.5 throughput rendering.

### 0.7 — Proxmox / vlan-aware bridges

Build on the port tags from 0.3 — show the `bridge-vlan-aware` flag's source
(`/etc/network/interfaces`) and tidy how a trunk vs. access port reads, so a
Proxmox node's VLAN topology is fully legible. Read-only first, as with Docker.

The shared-plumbing model note holds throughout: an 802.1q **VLAN**
subinterface, a **veth** virtual cable, and a **bridge port's VLAN filtering**
are three *different* kernel objects that share a "virtual" feel but each keep
their own correct name — we do *not* rebrand one as another.

### 0.8 — inbound firewall rules

Per interface, read from `nft -j list ruleset` (fallback `iptables-save`).
Display first; editing is a later, careful step.

### 0.9 — scale and polish

- Multiple hosts open at once (tabs), copy a config box *between hosts*
- Canvas search/filter for machines with many interfaces
- Saved "profiles": a set of draft boxes you can apply as a unit
- Accessibility and keyboard-only operation
- Translations

## Two bigger bets

These cut across the per-milestone work and are worth naming explicitly. Both
are candidate flagship directions, not yet slotted to a version.

### A CLI worth shipping on its own

NetGrip already does the hard part nobody else bundles: it **detects and
abstracts every distro's network backend** (NetworkManager, systemd-networkd,
netplan, ifupdown) behind one model and one plan format. Today that engine is
reachable only through the GUI. Exposed as a first-class command-line surface it
could stand on its own — a single portable tool that does the right thing across
distros that each disagree on how networking is configured:

- `netgrip show` — the live model as text / JSON (the demo CLI viewer is the
  seed);
- `netgrip plan <change>` / `netgrip apply` — build and run the same vetted
  plans from a script, with the same **Try / Apply / Save** semantics;
- `netgrip backend` — introspect what owns config and whether a change persists.

`core/` is already Qt-free and plan-first, so this is mostly surface, not a new
engine. This may be NetGrip's most differentiated feature.

### GUI by default when there's a display

Both NetGrip and DiskGrip should **launch the GUI automatically when a display
is available**, and fall back to the terminal view when headless — with explicit
`--gui` / `--cli` (or `--tui`) overrides for when auto-detect guesses wrong
(`DISPLAY` forwarded over SSH but unwanted, scripted runs, offscreen renders).
Detect via `DISPLAY` / `WAYLAND_DISPLAY` plus a Qt platform probe, so the single
`netgrip` command does the obvious thing everywhere without the user having to
choose.

## Experimental — the `claudegonewild` branch

A sandbox for bolder ideas that must prove themselves before landing on `dev`
(experiments only; `core/` stays Qt-free; pytest + ruff stay green; never merged
without review). Live / parked:

- ✓ **live RX/TX overlay** — graduated into 0.5;
- **rates, not totals** — sample twice and show bytes/sec plus tiny sparklines
  on busy links;
- a **diagnostics overlay** — carrier/flap state and error/drop counters from
  `ip -s`, surfaced on a NIC when something's wrong;
- **richer demo scenarios** — a Proxmox node, a Kubernetes host — to exercise
  rendering and serve as living screenshots;
- **CI screenshot generation** — render the demo to PNG headlessly (offscreen
  Qt, `MainWindow.grab()`) so the README screenshots regenerate on every release
  instead of by hand. *(We hit exactly this pain cutting 0.5.)*

## Distribution packaging

- Debian/Ubuntu packaging (`debian/`), aiming for inclusion in Debian — see
  [docs/PACKAGING.md](docs/PACKAGING.md)
- Fedora/openSUSE specs, AUR
- Flatpak (tricky: needs host network access; investigate the portal story)

## Someday / maybe

- Windows hosts (the core/UI split and runner abstraction were designed so a
  WinRM/PowerShell runner + netsh/NetAdapter backend can slot in)
- A polkit-authenticated helper daemon so unprivileged sessions get fine-grained
  authorization instead of blanket sudo
- Read-only "observer" mode for NOC wall displays
- A shared toolkit with **DiskGrip** (the gparted-flavoured sibling): both are
  "probe real state → dataclasses → plan-first mutations → flat themed canvas,
  GUI-or-CLI" — the runner, theme and plan-confirm layers want to converge.
