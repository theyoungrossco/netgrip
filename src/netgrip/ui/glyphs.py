"""Category glyphs as inline SVG — small, scalable line-art marks that tag a box
(and its legend row) with what kind of thing it is: a wired or wireless physical
NIC, a group (bond/bridge/team), a VLAN, the System DNS box, loopback.

Vector on purpose: the marks render straight into the painter through
``QSvgRenderer``, so they stay crisp at any canvas zoom instead of going soft
like a baked pixmap. Colour is injected from the theme at paint time (hard rule
4 — no hardcoded hex), which keeps them palette-aware and guarantees the canvas
and the legend draw the very same glyph.

UI-layer module: importing Qt here is fine (unlike ``core``).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from netgrip.ui import theme

# Common wrapper for every glyph: a 24x24 grid drawn in a single stroke colour
# ({c}), round-capped line art. Per-glyph bodies below fill in the shapes; a
# filled dot/hole sets fill="{c}" to override the no-fill default.
_HEADER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
)

# Each entry is the SVG body for one category glyph (see the legend table).
_BODIES: dict[str, str] = {
    # An RJ45 plug: a connector body, the cable above, contact pins below.
    "wired": (
        '<rect x="6" y="7" width="12" height="11" rx="1"/>'
        '<path d="M12 7V3"/>'
        '<path d="M9 18v2.5M12 18v2.5M15 18v2.5"/>'
    ),
    # The Wi-Fi fan: three arcs rising from a base dot.
    "wireless": (
        '<path d="M2 8.8a15 15 0 0 1 20 0"/>'
        '<path d="M5 12.9a10 10 0 0 1 14 0"/>'
        '<path d="M8.5 16.4a5 5 0 0 1 7 0"/>'
        '<circle cx="12" cy="20" r="1.2" fill="{c}" stroke="none"/>'
    ),
    # A switch/bridge: a device body with an uplink and three downward ports —
    # several links joined into one.
    "group": (
        '<rect x="3" y="9.5" width="18" height="5.5" rx="1.5"/>'
        '<path d="M12 9.5V6"/>'
        '<path d="M7.5 15v3M12 15v3M16.5 15v3"/>'
    ),
    # A luggage tag with its hole: a VLAN is a *tagged* subinterface.
    "vlan": (
        '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 '
        '.586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 '
        '0 0-3.42z"/>'
        '<circle cx="7.5" cy="7.5" r="1.4" fill="{c}" stroke="none"/>'
    ),
    # Two opposing arrows: a protocol is an agreed way to exchange data, the
    # shared meaning behind an IPv4/IPv6 address.
    "protocol": (
        '<path d="M4 9h13"/>'
        '<path d="M14 6l3 3-3 3"/>'
        '<path d="M20 15H7"/>'
        '<path d="M10 12l-3 3 3 3"/>'
    ),
    # A wireframe globe: name resolution across the wider network. Equator,
    # two latitude arcs and a slim central meridian — a full grid so it reads
    # as a globe rather than a lone eye.
    "dns": (
        '<circle cx="12" cy="12" r="9.5"/>'
        '<path d="M2.5 12h19"/>'
        '<path d="M5.2 7a16 16 0 0 0 13.6 0"/>'
        '<path d="M5.2 17a16 16 0 0 1 13.6 0"/>'
        '<path d="M12 2.5a4.6 9.5 0 0 0 0 19 4.6 9.5 0 0 0 0-19"/>'
    ),
    # A shipping container: a box with vertical ribs — the docker mark.
    "container": (
        '<rect x="3.5" y="6.5" width="17" height="11" rx="1"/>'
        '<path d="M8 6.5v11M12 6.5v11M16 6.5v11"/>'
    ),
    # A circular return arrow: traffic that loops back to the host itself.
    "loopback": (
        '<path d="M20.5 12a8.5 8.5 0 1 1-8.5-8.5c2.4 0 4.66.95 6.36 2.6L20.5 8"/>'
        '<path d="M20.5 3.5v4.5h-4.5"/>'
    ),
    # A padlock: WireGuard is a secure VPN tunnel — the lock marks it as
    # encrypted and distinct from a plain virtual/physical NIC.
    "tunnel": (
        '<rect x="6" y="11" width="12" height="9" rx="1.5"/>'
        '<path d="M9 11V8a3 3 0 0 1 6 0v3"/>'
        '<circle cx="12" cy="16" r="1.2" fill="{c}" stroke="none"/>'
    ),
}

# (glyph, colour name) -> renderer. Colour only changes on a light/dark switch,
# so this stays a handful of entries; renderers are reusable across paints.
_cache: dict[tuple[str, str], QSvgRenderer] = {}


def _renderer(glyph: str, color: QColor) -> QSvgRenderer | None:
    body = _BODIES.get(glyph)
    if body is None:
        return None
    key = (glyph, color.name())
    renderer = _cache.get(key)
    if renderer is None:
        svg = (_HEADER + body + "</svg>").format(c=color.name())
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        _cache[key] = renderer
    return renderer


def paint(painter: QPainter, rect: QRectF, glyph: str, color: QColor) -> None:
    """Render ``glyph`` into ``rect`` in ``color`` (no-op for an unknown key)."""
    renderer = _renderer(glyph, color)
    if renderer is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, rect)
    painter.restore()


class GlyphWidget(QWidget):
    """A glyph as a standalone widget, for the legend rows. Re-reads the tint
    from the theme on every paint, so a light/dark switch just needs ``update``."""

    def __init__(self, glyph: str, size: int = 16, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint(painter, QRectF(0, 0, self._size, self._size), self._glyph, theme.text_dim())
        painter.end()
