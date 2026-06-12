# NetGrip

**Visual, drag-and-drop network interface management for Linux.**

NetGrip shows your machine's network as it actually is: NICs are boxes, and
every piece of configuration — an IPv4 setup, an IPv6 setup, a VLAN, a bond —
is its own box, joined to its interface by a line. Reconfiguring the network
is direct manipulation:

- **Drag an IP box** from one NIC to another and the addresses move with it.
- **Ctrl-drag** to clone a configuration instead of moving it.
- **Drag one NIC onto another** to create a bond (failover, LACP, and the
  other kernel bonding modes).
- **Right-click** a NIC to add a VLAN or an IP config; right-click a bond to
  change its mode or membership.
- **Detach** an IP config and it becomes a floating *draft* you can park on
  the canvas and attach somewhere else later.
- Stack them: an IP config attached to a VLAN attached to a bond of two NICs.

It manages the local machine, or — using your existing SSH config, keys and
agent — any remote Linux machine you can reach with `ssh`.

![NetGrip showing two NICs bonded with LACP, a VLAN on the bond, and IP config boxes](docs/img/screenshot-demo.png)

## How it works

NetGrip reads network state with `ip -json` and applies changes with plain
`iproute2` commands. Before anything is changed, it shows you the **exact
commands** it is about to run and asks for confirmation — what you approve is
what executes, locally via `sudo`/`pkexec` or remotely via `ssh`.

> **Important:** NetGrip 0.1 manipulates the *running* network stack.
> Changes are real and immediate, but they are **not persisted across
> reboots** yet. Persistence backends (NetworkManager, systemd-networkd,
> netplan) are on the [roadmap](ROADMAP.md).

## Installing

NetGrip is alpha software. From source:

```sh
git clone https://github.com/theyoungrossco/netgrip.git
cd netgrip
python3 -m venv .venv && .venv/bin/pip install .
.venv/bin/netgrip
```

or with [pipx](https://pipx.pypa.io/): `pipx install git+https://github.com/theyoungrossco/netgrip.git`

Requirements: Linux, Python ≥ 3.10, iproute2 ≥ 4.14 (any distro from the
last several years). Remote hosts need only `iproute2` and an SSH server.

Distribution packages (apt and friends) are a stated goal — see
[docs/PACKAGING.md](docs/PACKAGING.md).

## Trying it safely

```sh
netgrip --demo
```

starts with canned interfaces. Every gesture works and shows the command
plan it *would* run, but nothing is executed. This is the best way to learn
the UI without touching your network.

## Usage

```sh
netgrip                  # manage this machine
netgrip --host user@box  # manage a remote machine over ssh
```

The **Host** dropdown lists the machine itself plus every concrete `Host`
alias found in your `~/.ssh/config` (Includes are followed). Pick one to
manage it — nothing is installed on the remote side.

### Privileges

Reading network state needs no privileges. Applying changes does:

- **Locally:** NetGrip runs as your user and escalates per action using
  passwordless `sudo` if available, otherwise `pkexec` (polkit). Running
  `sudo netgrip` also works.
- **Remotely:** the SSH user must be root or have passwordless sudo, because
  SSH runs in batch mode (no interactive password prompts).

Each user action — however many commands it expands to — is applied as a
single confirmed batch.

## Status

Working today: viewing interfaces/addresses/VLANs/bonds/bridges, moving and
cloning IP configs, creating and deleting VLANs, creating bonds by drag or
dialog, bond mode and membership changes, link up/down, draft IP configs,
remote hosts over SSH, demo mode.

See [ROADMAP.md](ROADMAP.md) for where this is going (persistence, routes
and gateways, bridges/teams, Windows hosts) and
[CHANGELOG.md](CHANGELOG.md) for history.

## Contributing

Contributions are very welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for
the development setup and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a
tour of the code. The backend core is plain Python with no Qt dependency,
so there is plenty to do even if GUI code isn't your thing.

## License

[GPL-3.0-or-later](LICENSE).
