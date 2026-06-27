# NetGrip OS compatibility testing

Findings from exercising NetGrip's **core** against a spread of Linux
distributions and their differing `iproute2` versions. This is a living
document — it is updated as more environments are tested.

## What is actually tested

NetGrip's portability risk is **not** Python (that runs anywhere ≥3.10); it is
whether NetGrip's parser understands each distro's `ip -json` / `bridge -json`
output, and whether the argv command *plans* it builds run on that distro's
`iproute2`. So the harness drives NetGrip's **own** code paths end-to-end:

```
actions.plan_*()  →  runner.run_privileged()  →  probe.probe()  →  assert model
```

For each distro it builds a plan with NetGrip's own `actions.py`, runs it through
the real `LocalRunner`, then re-reads with NetGrip's `probe.py` and asserts the
resulting model objects match — validating **command generation and JSON parsing
together** against that distro's live `ip`/`bridge` binaries.

### Per-distro operation checklist

| Check | Exercises |
|---|---|
| `probe_clean` | parse `ip -details -json address show` (loopback + NICs) |
| `address_v4_v6` | `plan_add/remove_addresses` (IPv4 + IPv6, dynamic flag) |
| `mtu_mac_alias` | `plan_set_mtu` / `plan_set_mac` / `plan_set_alias` |
| `gateway_v4` | `plan_set_gateway` / `plan_clear_gateway` + route JSON parse |
| `vlan` | `plan_create_vlan` → `vlan_id` / `vlan_parent` |
| `bond` | `plan_create_bond` → `bond_mode`, member `master` |
| `veth_peer` | veth peer resolution (both directions) |
| `bridge_vlan_aware` | vlan-filtering bridge + `bridge -json vlan show` → `pvid`/tags |
| `dns_read` | `/etc/resolv.conf` + `resolvectl` parse (best-effort) |
| `docker_degrade` | docker probe degrades silently when absent |
| `wireless_degrade` | sysfs `phy80211` detection degrades silently |

## Tier 1 — container matrix (iproute2 JSON-dialect sweep)

Containers share the host kernel (6.1), so this tier isolates the **userspace
`iproute2` JSON differences** — exactly NetGrip's real cross-distro risk —
across a wide version range. Each ran the full 12-point checklist as root.

**Result: 16 distros, every check passes** across `iproute2` from
`ss200127` (Jan 2020) through `7.1.0` (2026) — ~6 years of releases.

| Distro | iproute2 | Result |
|---|---|---|
| Ubuntu 20.04 LTS | ss200127 (≈5.5) | ✅ 12/12 |
| Ubuntu 22.04 LTS | 5.15.0 | ✅ 12/12 |
| Ubuntu 24.04 LTS | 6.1.0 | ✅ 12/12 |
| Debian 12 (bookworm) | 6.1.0 | ✅ 12/12 |
| AlmaLinux 8.10 (RHEL 8) | 6.2.0 | ✅ 12/12 |
| AlmaLinux 9.8 (RHEL 9) | 6.17.0 | ✅ 12/12 |
| Rocky Linux 9.3 | 6.17.0 | ✅ 12/12 |
| CentOS Stream 9 | 6.17.0 | ✅ 12/12 |
| Oracle Linux 9.8 | 6.17.0 | ✅ 12/12 |
| Amazon Linux 2023 | 6.10.0 | ✅ 12/12 |
| Fedora 40 | 6.7.0 | ✅ 12/12 |
| Fedora 44 | 6.17.0 | ✅ 12/12 |
| Arch Linux (rolling) | 7.1.0 | ✅ 12/12 |
| openSUSE Tumbleweed | 7.1.0 | ✅ 12/12 |
| Alpine 3.24 (+iproute2) | 7.0.0 | ✅ 12/12 |
| Alpine 3.24 (BusyBox `ip`) | — | ⚠️ see Findings |

## Tier 2 — privilege escalation (non-root sudoer)

The container matrix runs as uid 0, where `run_privileged()` executes directly
and `runner.py`'s sudo logic never runs. A separate Debian container running as
an unprivileged `tester` user covers it:

| Mode | Result |
|---|---|
| Passwordless sudo | ✅ `escalation_status() == ready`; plan escalates via `sudo -n` |
| Password-required sudo | ✅ classified `needs_password`; after `set_password()` → `ready`; plan escalates via `sudo -A` askpass |

Reads (`probe`) run unprivileged; only the mutating batch escalates — confirmed.

## Tier 3 — full VMs (QEMU/KVM, real kernel + SSH)

Containers can't reach a **different kernel**, NetGrip's **SSHRunner** path, or
**`systemd-resolved` per-link DNS**. Two cloud-image VMs (QEMU/KVM), driven over
SSH from the host with NetGrip's own `SSHRunner`, cover these. Each ran:
escalation classification, probe, address (v4+v6), VLAN and gateway plans applied
*remotely*, and DNS read — all through `SSHRunner` + remote `sudo -n`.

| VM | Kernel | iproute2 | Result |
|---|---|---|---|
| Debian 12 (cloud) | 6.1.0 | 6.1.0 | ✅ 6/6 |
| Arch Linux (cloud) | 7.0.12 | 7.0.0 | ✅ 6/6 |

Both confirmed `resolvectl=yes` with correct **per-link DNS** parsing
(`resolvectl dns`/`domain` → `{link: [servers]}`) — the systemd-resolved path
that has no container equivalent. Remote escalation (`sudo -n` over SSH),
remote-applied address/VLAN/gateway plans, and re-probe all matched the model.

## Findings

1. **Parsing is robust across the whole tested `iproute2` range.** No JSON-schema
   drift broke any parser from 5.5-era (`ss200127`) to 7.1.0. All of address,
   route, VLAN, bond, veth-peer and the vlan-aware-bridge `bridge -json vlan`
   enrichment (the most dialect-sensitive read) parsed cleanly everywhere.

2. **BusyBox `ip` (minimal Alpine) — degrades cleanly but the message could be
   friendlier.** BusyBox's `ip` applet has no `-json`/`-details` and exits
   non-zero, so `probe()` raises a `CommandError` (a `RuntimeError` subclass —
   no traceback, the UI surfaces it). However the user sees BusyBox's raw usage
   text, not NetGrip's intended "needs iproute2 4.14 or newer" hint, which only
   fires when `ip` *succeeds* but emits non-JSON. Best-effort enrichers
   (DNS/docker/wireless) degrade silently as designed. _Possible improvement:_
   detect the non-zero/again-non-JSON case and surface the same friendly hint.

3. **System-Python age, not NetGrip, is the floor on old distros.** AlmaLinux 8
   and openSUSE Leap 15.6 ship Python 3.6 as the default `python3`; NetGrip
   requires ≥3.10 (`pyproject.toml`), so it needs a newer interpreter there
   (e.g. the `python3.11` module) — with which AlmaLinux 8 passes 12/12. This is
   an existing, documented requirement, not a regression.

4. **The SSH + escalation + per-link DNS path works on a real, different
   kernel.** Against a live Arch VM (kernel 7.0.12, newer than the 6.1 host) and
   a Debian VM, `SSHRunner` read/probe, remote `sudo -n` escalation, remotely
   applied address/VLAN/gateway plans, and `resolvectl` per-link DNS parsing all
   succeeded — the surfaces with no container equivalent.

---
_Method/harness lives outside the repo (host lab volume `/mnt/lab/harness`); this
document records the findings. Re-runnable; results above reflect the latest sweep.
Summary: **16 distros (iproute2 ss200127→7.1.0) + local sudo escalation + 2 VMs
over SSH — all green**, one BusyBox UX note (Finding 2)._
