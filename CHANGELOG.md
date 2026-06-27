# Changelog

All notable changes to NetGrip are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-06-27

### Added

- **WireGuard interfaces**: WireGuard tunnel interfaces (kernel link kind
  `wireguard`) are now recognised and drawn as virtual interfaces with their own
  glyph, so a `wg0` appears on the canvas like any other link. (Display only —
  peers and endpoints aren't shown yet.)
- **Live interface throughput**: each interface box now shows live RX/TX byte
  counters, read unprivileged from `ip -s -j link show`.
- **Host-network containers**: containers running with `network_mode: host`
  (e.g. Plex) now show "host's network" in their box and connect to the host's
  uplink IPv4 group with a solid purple line — distinct from the grey member
  cables and the dotted egress line. The generic egress line is suppressed for
  these containers since the new line already expresses the relationship. A
  "Host network" entry is added to the legend.
- **VM tap label**: KVM/QEMU tap ports (a `tun` device enslaved to a bridge,
  e.g. `vnet5`) now display "vm tap" instead of the raw kernel type "tun",
  making their role legible without full hypervisor support.

### Fixed

- **Empty docker bridges hidden**: docker bridge interfaces with no running
  containers (e.g. a compose project's default network whose containers all use
  host networking, or an idle `docker0`) no longer appear as disconnected
  floating islands on the canvas.

## [0.4.2] - 2026-06-24

A packaging fix for the Windows build.

### Fixed

- **Windows app icon**: the running app's window and taskbar icon showed a
  generic network glyph instead of the NetGrip monogram. The PyInstaller build
  wasn't bundling the monogram SVG that `app_icon()` loads via
  `importlib.resources`, so it fell back to the themed `network-wired` icon.
  The frozen app now ships `netgrip.resources`, so the monogram appears. (The
  installer/shortcut `.ico` was already correct; a stale Windows icon cache
  from a prior install can still show the old shortcut icon until rebuilt.)

## [0.4.1] - 2026-06-24

A maintenance release between milestones: remote hosts that need a sudo
password are now manageable, runtime-only ifupdown boxes can gain a persistence
backend in one click, and the canvas exports to SVG/PDF.

### Added

- **Enable persistence on a runtime-only host, in one click**: when NetGrip
  detects a Debian/Proxmox box running classic **ifupdown** (an
  `/etc/network/interfaces` file, but no `ifreload`) on an `apt` host, the
  status-bar **Persist** indicator becomes clickable. Clicking it installs
  **ifupdown2** through the normal confirm → elevate → run → re-probe flow; the
  re-probe then re-detects the backend, so the indicator flips from *Runtime
  only* to *ifupdown* and **Save** starts persisting — no shell required.
- **Remote sudo password over SSH**: privileged actions on a host that requires
  a password for `sudo` now prompt for it (once, cached for the session) and run
  via `sudo -S`, with the password sent only over the SSH channel's stdin —
  never the command line, the remote environment, or disk. Previously a remote
  host needed root login or passwordless sudo, so managing a password-sudo box
  from a client without a local `sudo` (e.g. Windows) failed silently with no
  prompt. Root login and passwordless sudo still work unchanged.
- **Export diagram** (`File ▸ Export diagram…`): save the canvas — exactly as
  shown, every visible box, line and glyph in its on-screen colour over the
  themed background — to a vector **SVG** or **PDF**. SVG is sized to the
  content (1:1, infinitely scalable); PDF is a standard **US Letter** page
  (oriented to suit the diagram) with the topology scaled to fit, so it prints
  without fuss. The **legend**, when shown, is placed in its own reserved
  column so it never overlaps the diagram, and a small **NetGrip monogram** is
  pinned bottom-right — giving you an at-a-glance network document for free.

## [0.4.0] - 2026-06-23

**0.4 — Docker visibility**, a milestone scoped to a single feature: make the
container layer legible, read-only. The next milestone (0.5, Proxmox /
vlan-aware bridges) begins from here. See [ROADMAP.md](ROADMAP.md).

### Added

- **Docker visibility** (0.4, read-only): a `docker0` / `br-…` bridge is now
  labelled with its **docker network** name, and each running **container** is
  drawn as a **single box** on the bridge network(s) it joins, showing its
  **image**, its **IP per network** and its **compose project / service**. A
  container's anonymous host-side `veth` is **folded into that box** rather than
  drawn beside it. A container's L3 lines now land on a **protocol (IP-config)
  box**, never the bare NIC, since both forwarding and the default route are
  address-level: **published ports** draw as a **dashed** connector to the box
  holding the host address they bind to — the specific address's box when a
  publish is pinned, else the uplink's box for that family — with ports sharing a
  box listed **one per line**, each as a `:8080→80/tcp` label (host-IP-prefixed
  when pinned), revealed when either end is **selected**. Every container also
  shows its always-on **outbound default route** as a distinct **dotted**,
  accented line to the uplink's IPv4 box (no ports, so no numbers), suppressed
  where a published-port line already reaches that same box. Both line kinds are
  independently hideable from the **View** menu (*Show published ports* / *Show
  default routes*), and the **legend** now keys all three connector styles
  (solid member link, dashed ports, dotted default route). Read best-effort via
  `docker network inspect` / `docker inspect`, so
  a host without docker — or without daemon access — is unaffected. Docker-owned
  links are **read-only** (a docker bridge and its members): netgrip refuses to
  rename, delete, re-address, add members to or move addresses off them, since
  that would break docker — edit those through docker / compose. A docker bridge
  is now titled by its **network name** (its alias if set, else the docker
  network, else the `br-…` ifname kept as a detail line), and the whole docker
  subgraph lays out **left-to-right from the host's uplink** (uplink → IPv4 box →
  containers → bridge), never with a bridge stuck in the left column. See
  [docs/0.4-PLAN.md](docs/0.4-PLAN.md).

### Fixed

- A container `veth` no longer mis-reports its peer as a host interface (e.g.
  `eth0`): the peer resolver now respects network namespaces, so a cross-netns
  ifindex that happens to collide with a host interface's is left unpaired.
- **The window now shows the NetGrip monogram, not a generic network glyph.**
  The icon is bundled inside the package (`netgrip/resources/`) and set on the
  application, so it appears however netgrip is launched — including straight
  from the venv's `bin/netgrip` with no desktop integration installed — instead
  of the previous hardcoded freedesktop `network-wired` fallback. That same SVG
  is now the single source the Linux installer and the Windows `.ico` generator
  read from.

### Changed

- **Canvas layout spaces out fan-outs**: when a box connects to several boxes in
  the next column, the gap after its column widens so the connector lines spread
  rather than overlap into one another.
- **New app icon** — an "N" monogram with four coloured nodes — replacing the
  previous mark across the README, the Linux desktop/scalable icon, the Windows
  `.ico` (executable, shortcut and installer), and the GitHub social preview.

## [0.3.0] - 2026-06-21

The work since 0.1.0 in one alpha release: persistence (Try / Apply / Save
through the host's real backend), per-family addressing, a clarity & terminology
pass, Windows as an SSH client, and one-command installers for Linux and Windows.

### Added

- **Save across reboots through the host's own backend** — netplan,
  systemd-networkd, NetworkManager or ifupdown, auto-detected — with a
  status-bar indicator of which backend owns the host and whether a change will
  persist.
- **Try / Apply / Save** for every mutation: **Apply** changes the running stack
  (as before), **Try** applies then auto-reverts after a countdown unless you
  keep it (a safety net against locking yourself out of a remote box), **Save**
  persists through the detected backend.
- **Persistent link properties** — rename, alias, MAC and MTU written as
  systemd `.link` (udev) files beneath whichever backend owns addressing.
- **Sudo password caching** so a multi-command action escalates at most once.
- **IPv4/IPv6 protocol settings dialog** with a **DHCP enabled/disabled** toggle,
  per-field Dynamic/Static pinning, and a *use DNS from DHCP* toggle (saved as
  `ignore-auto-dns` / `UseDNS=no` / `use-dns: false`).
- **Per-link DNS** read via `resolvectl` for resolver provenance, bucketed into
  the IPv4/IPv6 group it belongs to; **manually added host-wide resolvers**,
  persisted per host and shown in the System DNS box.
- **Draft VLANs**: right-click the canvas to create a VLAN that does not exist
  yet, give it an id, a name and addresses, then drag it onto a parent NIC or
  bond to create it — addresses and all — in one batch. Persisted per host.
- **veth pairs drawn as a single shared cable** (peer matched from
  `ip -d -json link`), so a container's `veth` lands visibly on its host bridge.
- **vlan-aware bridge port tags** shown read-only (PVID + tagged lists).
- **Topology-aware canvas layout** that orders boxes to cut crossing connectors.
- **View menu** — Show loopback (moved off the toolbar), **Hide offline**, and
  **Legend** — plus Refresh as an icon and a right-aligned **?** help button.
- **Legend overlay**: a floating, toggleable colour key for the box categories.
- **Wired / Wireless glyph** on physical NICs, detected from sysfs
  (`/sys/class/net/*/phy80211`).
- **Windows support as an SSH-only client**: the *Local* option is hidden, hosts
  come from `~/.ssh/config`, and SSH host-key prompts and password login are
  handled gracefully.
- **Installers**: `scripts/install-linux.sh` installs NetGrip (pipx or a private
  venv) and adds it to the application menu; a Windows `setup.exe`
  (PyInstaller + Inno Setup); `scripts/release.sh` and a Release CI workflow that
  rebuild both on a `vX.Y.Z` tag.

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

[Unreleased]: https://github.com/theyoungrossco/netgrip/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/theyoungrossco/netgrip/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/theyoungrossco/netgrip/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/theyoungrossco/netgrip/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/theyoungrossco/netgrip/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/theyoungrossco/netgrip/compare/v0.1.0...v0.3.0
[0.1.0]: https://github.com/theyoungrossco/netgrip/releases/tag/v0.1.0
