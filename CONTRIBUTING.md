# Contributing to NetGrip

Thanks for considering a contribution! Bug reports, packaging help, docs and
code are all welcome.

## Development setup

```sh
git clone https://github.com/theyoungrossco/netgrip.git
cd netgrip
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Run it:

```sh
.venv/bin/netgrip --demo   # safe sandbox, no commands executed
.venv/bin/netgrip          # against your real machine
```

Test and lint (both must pass; CI enforces them):

```sh
.venv/bin/pytest
.venv/bin/ruff check src tests
```

## Code layout

```
src/netgrip/core/   backend: model, probing, command planning/execution
src/netgrip/ui/     Qt: canvas, items, dialogs, main window
tests/              unit tests for the core
```

Two rules keep the project healthy:

1. **`core/` never imports Qt.** It must stay testable headless and usable
   from scripts. The unit tests run without PySide6 installed.
2. **Every mutation is a plan first.** Functions in `core/actions.py` build
   command lists; nothing executes until the user has seen and confirmed the
   exact commands. New operations should follow the same pattern.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the longer tour.

## Style

- `ruff` settings live in `pyproject.toml` (line length 100, py310+).
- Prefer plain, boring code over cleverness; this tool runs as root.
- UI stays visually flat: rectangles and straight lines.

## Submitting changes

1. Fork, branch from `main`.
2. Add or update tests for anything in `core/`.
3. Open a pull request describing *what* and *why*; small PRs review faster.

For substantial features (new backends, persistence, protocol support),
please open an issue first so the approach can be discussed.

## Reporting bugs

Include your distro, `ip -V` output, whether the host was local or SSH, and
the exact command plan NetGrip showed in its confirmation dialog if the
failure happened while applying changes.
