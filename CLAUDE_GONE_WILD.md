# Claude Gone Wild — experimental sandbox branch

This branch (`claudegonewild`) is the autonomous agent's playground for ideas
that are too speculative or opinionated to go straight onto `dev`. Each session
picks one focused experiment, implements it, and pushes here for Ross's review.

## Ground rules

- **Purpose**: exploration only. Nothing on this branch is a commitment to ship.
- **Core must stay Qt-free** — `src/netgrip/core/` never imports Qt. Unit tests
  run headless; keep them that way.
- **Tests must pass**: `.venv/bin/pytest` and `.venv/bin/ruff check src tests`
  must be green after every commit.
- **Patterns still apply**: every mutation is a plan first; no hardcoded colours;
  one batch per user action; no stacked modals.
- **Never merged without Ross's review.** This branch may be rebased, squashed,
  or cherry-picked onto `dev` — or just discarded. Either outcome is fine.

## Experiment log

### Session 1 — WireGuard interface support

**What**: First-class rendering of WireGuard (`wg*`) tunnel interfaces.

WireGuard is ubiquitous but absent from the demo and unrecognised visually —
it falls back to a plain NIC box. This session adds:

- `"wireguard"` colour pair to `theme.py` (distinct purple-teal, light+dark)
- `"tunnel"` glyph to `glyphs.py` (padlock mark — VPN/secure tunnel)
- `NicNode` upgraded to pick the wireguard theme + glyph when `kind="wireguard"`
- `wg0` added to the demo scenario (VPN tunnel subnet, no MAC, MTU 1420)
- Test fixture in `test_probe.py` verifying that a WireGuard interface without
  an `address` field parses cleanly (the real-world case)

**What wasn't verified**: UI rendering — no display available in the unattended
session. The glyph shape and colour look are unconfirmed until Ross runs the demo.

**Next experiment idea**: live interface statistics overlay — read RX/TX bytes
from `ip -s -j link show` and surface them as a dim annotation on each NIC box.
This is a genuinely experimental read path (the probe command changes) and a new
UI annotation pattern, neither of which belongs on `dev` without more thought.
