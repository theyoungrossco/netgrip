# Agent & contributor guide

Orientation for anyone (human or AI) working on NetGrip. Read this first; it
points at the deeper docs rather than repeating them.

## 30-second orientation

NetGrip is two layers:

- **`src/netgrip/core/`** — plain Python, **no Qt**. Probes a host with
  `ip -json`, holds the result in dataclasses (`model.py`), and builds
  iproute2 command *plans* (`actions.py`) without executing anything.
- **`src/netgrip/ui/`** — PySide6. Draws the model as flat boxes + lines,
  turns gestures into plans, shows each plan verbatim, and hands it to a
  *runner* (`core/runner.py`) to execute.

The whole loop, every time:

```
gesture (drag / menu)
  → core.actions.plan_*()            build argv lists, pure, testable
  → ui.dialogs.confirm_commands()    user sees the exact commands
  → core.runner.run_privileged()     one escalated sh -c batch
  → core.probe.probe()  →  ui.canvas.populate()   re-read & redraw
```

The canvas always shows freshly re-probed reality, never an optimistic guess.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full tour and
[ROADMAP.md](ROADMAP.md) for where this is going.

## Hard rules (don't break these)

1. **`core/` never imports Qt.** It must stay headless-testable; the unit
   tests run without PySide6 installed. UI-only state that needs disk goes
   through `core/store.py`, which uses only `os`/`pathlib`/`json`.
2. **Every mutation is a plan first.** A change is a `plan_*()` in
   `core/actions.py` returning `list[list[str]]` (a list of argv lists).
   Nothing runs until the user has confirmed the exact commands. Demo mode
   shows real plans but refuses to run them.
3. **One batch per user action.** `Runner.run_privileged()` joins a plan with
   `&&` into a single `sh -c`, so escalation prompts at most once and a
   failing step aborts the rest. Keep a gesture's commands in one plan.
4. **Flat *network view*, themed *look*.** "Flat" means the topology is drawn
   plainly — interfaces as rectangles joined by straight, centre-to-centre
   lines (not a node-editor with curved "cables"). It does **not** mean drab:
   colours must come from `ui/theme.py` (palette-driven, light/dark aware) so
   the canvas matches the OS theme. Never hardcode hex colours in items/canvas;
   add them to `theme.py` instead.
5. **A dialog never opens another dialog.** No stacked modals, ever. Report
   invalid input *inline* (see `dialogs._error_label`), not with a popup. The
   apply → confirm flow is fine because each input dialog has closed before the
   confirmation dialog opens — that's sequential, not nested.
   - For a value that can be auto-assigned (DHCP/RA) or set by hand — gateway,
     DNS — use `dialogs.DynamicStaticField`: a **Dynamic** radio shows the live
     value greyed out and means "leave it alone", **Static** enables a custom
     entry. Disable Static where it can't be applied (e.g. DNS without
     systemd-resolved).
6. **Never block the UI thread.** Probes and applies run through
   `ui/worker.run_in_background`; every apply is followed by a fresh probe.

## Recipes (extend without re-reading everything)

**Add a field read from the host**
1. Add it to the relevant dataclass in `core/model.py`.
2. Parse it in `core/probe.py` (from `ip -details -json …` output).
3. Add a representative value to `core/demo.py` and to the fixture in
   `tests/test_probe.py`; assert it parses.

**Add a mutation (a new thing the user can change)**
1. Write `plan_<verb>(…) -> list[list[str]]` in `core/actions.py`; add an
   input validator (e.g. `valid_mac`) if it takes free-form text.
2. Unit-test the plan shape in `tests/test_actions.py` (this is the dangerous
   part; it must be tested).
3. Wire a menu item or dialog in `ui/main_window.py` that builds the plan and
   calls `self._apply(title, plan)` — that handles confirm → run → re-probe.

**Add a canvas node type**
1. Subclass `BaseNode` in `ui/items.py`; set a stable `.key` (used to
   remember positions). Pull fill/border from `theme.node(...)` (add a new
   entry to the light + dark tables in `ui/theme.py`), never a literal colour.
2. Create the node and its `Edge` in `Canvas.populate` (`ui/canvas.py`).

## Where state lives

- **Kernel** owns real config: addresses, MTU, MAC, the interface **alias**
  (`ifalias`), and the **default gateway** (a default route, read from
  `ip -json route show`, set with `ip route replace default …`). MAC is a
  link-layer property — it lives on interfaces, never on an IP-config box.
  Runtime-only for now — reboot persistence is the 0.2 backend (see ROADMAP).
- **DNS** is read from `/etc/resolv.conf` (works everywhere, local or SSH) and
  shown as a system box. Per-link DNS is *set* via `resolvectl` only where
  systemd-resolved exists (`HostState.can_edit_dns`); portable, persistent DNS
  is a 0.2 backend job.
- **App metadata** (`core/store.py`, JSON under
  `${XDG_DATA_HOME:-~/.local/share}/netgrip/<host>.json`) owns UI state the
  kernel can't hold: draft configs, remembered box positions, and free-form
  names given to IP-config boxes. Keyed per host label.

## Commands

```sh
.venv/bin/pytest                  # core unit tests (no Qt needed)
.venv/bin/ruff check src tests    # lint (line length 100, py310+)
.venv/bin/netgrip --demo          # safe sandbox; every gesture, no execution
```

Both pytest and ruff must pass; CI enforces them. Add/update tests for
anything in `core/`. See [CONTRIBUTING.md](CONTRIBUTING.md) for PR workflow.
