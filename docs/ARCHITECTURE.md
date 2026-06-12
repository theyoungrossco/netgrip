# Architecture

## The one-paragraph version

NetGrip is two layers. `netgrip.core` is plain Python: it probes a host with
`ip -json`, holds the result in dataclasses, and builds iproute2 command
plans for every change — without executing anything. `netgrip.ui` is PySide6
(Qt): it draws the model as flat boxes and lines, turns drag/menu gestures
into plans from the core, shows each plan to the user verbatim, and only
then hands it to a *runner* for execution. The runner is the only thing that
differs between managing localhost and managing a remote machine.

```
 gesture (drag / menu)
        │
        ▼
 ui.main_window ──► core.actions.plan_*()      (build argv lists, pure)
        │
        ▼
 dialogs.confirm_commands()                    (user sees exact commands)
        │
        ▼
 core.runner.Runner.run_privileged(plan)       (one escalated batch)
        │
        ▼
 core.probe.probe(runner) ──► ui.canvas.populate()   (re-read & redraw)
```

## Core (`src/netgrip/core/`) — no Qt allowed

| module | role |
|---|---|
| `model.py` | `Interface`, `Address`, `HostState` dataclasses |
| `probe.py` | parse `ip -details -json address show` into the model |
| `actions.py` | `plan_*()` functions returning `list[list[str]]` command plans |
| `runner.py` | `LocalRunner`, `SSHRunner`, `DemoRunner` |
| `sshhosts.py` | `~/.ssh/config` Host alias discovery |
| `demo.py` | canned interfaces for demo mode |

Design decisions worth knowing:

- **iproute2 JSON is the wire format.** It is identical locally and over
  SSH, available everywhere since ~2017, and spares us scraping text. The
  same probe code therefore serves both runners, and a future Windows
  backend is "just" another runner + probe + planner.
- **Plans, not calls.** Mutations are data until confirmed. This gives the
  confirmation dialog for free, makes the dangerous part trivially testable,
  and means demo mode can show real plans while refusing to run them.
- **One batch per user action.** `run_privileged()` joins a plan with `&&`
  into a single `sh -c` invocation, so sudo/pkexec authenticates at most
  once per gesture, and a failing step aborts the rest.
- **SSH is the system client, not a library.** Users' jump hosts, agents,
  certificates and known_hosts policies work without NetGrip knowing
  anything about them. BatchMode prevents hangs; the cost is that remote
  sudo must be passwordless.

## UI (`src/netgrip/ui/`)

| module | role |
|---|---|
| `items.py` | `BaseNode` (flat rectangle), `NicNode`, `GroupNode`, `VlanNode`, `IpNode`, `Edge` (straight line) |
| `canvas.py` | scene population, tree auto-layout, drop-target detection, drafts |
| `main_window.py` | host picker, context menus, gesture → plan → confirm → apply |
| `dialogs.py` | address/VLAN/bond input with validation, command confirmation |
| `worker.py` | run probes and applies on a thread pool, signal back to the UI |

Notes:

- The visual language is deliberately flat: rectangles and straight lines.
  Resist the urge to add gradients, curves or shadows.
- An `IpNode` is *all addresses of one family on one interface*. Finer
  granularity (per-address boxes) was considered and rejected for clutter;
  the Edit dialog covers the rare per-address operation.
- Auto-layout is a simple DFS tree walk: column = depth (NIC → bond/VLAN →
  IP), row = subtree height. User-moved boxes keep their positions across
  refreshes (`Canvas._positions`); "Auto-layout" resets them.
- Drops count only when the dragged box overlaps a valid target by ≥35% of
  its own area; anything less is just repositioning.
- The UI never blocks on the network: probes and applies run in
  `worker.run_in_background`, and every apply is followed by a fresh probe —
  the canvas always shows re-read reality, never an optimistic guess.

## Testing

`tests/` covers the core only (parsing, plan construction, quoting, ssh
config discovery) and runs without PySide6, which keeps CI light. UI testing
is currently manual: `netgrip --demo` exercises every gesture safely.
