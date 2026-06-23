"""The legend: a small floating colour key for the canvas.

Pinned to the canvas's top-left corner (mirroring the Save button top-right),
toggled from the View menu and persisted in QSettings. Every colour is pulled
live from ``theme.py`` and re-applied on a light/dark switch, so the swatches
always match the boxes they explain. Rows come from ``theme.LEGEND_CATEGORIES``,
the single table the Definitions page (workstream E) shares.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QMenu, QWidget

from netgrip.ui import glyphs, theme


class _LineSwatch(QWidget):
    """A tiny horizontal line in a connector's pen — the legend key for one
    canvas line style. Reads its pen live from ``theme.line_pen`` on every
    repaint, so it follows a light/dark switch like the colour swatches do."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(18, 12)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(theme.line_pen(self._kind))
        y = self.height() / 2
        painter.drawLine(QPointF(1, y), QPointF(self.width() - 1, y))


class Legend(QFrame):
    """A floating key mapping each box colour (and glyph) to its category."""

    #: Emitted when the user picks "Hide legend" from the right-click menu. The
    #: window unchecks the View toggle in response, so visibility stays in sync.
    hide_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("legend")
        self._rows: list[tuple[QLabel, str]] = []  # (swatch, colour key)
        self._labels: list[QLabel] = []
        self._glyphs: list[glyphs.GlyphWidget] = []
        self._line_swatches: list[_LineSwatch] = []

        grid = QGridLayout(self)
        grid.setContentsMargins(11, 9, 13, 9)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(5)
        row = 0
        for row, (label, key, _hint, glyph) in enumerate(theme.LEGEND_CATEGORIES):
            swatch = QLabel()
            swatch.setFixedSize(18, 12)
            grid.addWidget(swatch, row, 0)
            if glyph:
                widget = glyphs.GlyphWidget(glyph, size=16)
                grid.addWidget(widget, row, 1)
                self._glyphs.append(widget)
            text = QLabel(label)
            grid.addWidget(text, row, 2)
            self._rows.append((swatch, key))
            self._labels.append(text)

        # The line-style key sits below the box colours, set off by a rule: the
        # three kinds of connector the canvas draws (see theme.line_pen).
        self._separator = QFrame()
        self._separator.setFrameShape(QFrame.Shape.HLine)
        grid.addWidget(self._separator, row + 1, 0, 1, 3)
        for offset, (label, kind, _hint) in enumerate(theme.LEGEND_LINES):
            swatch = _LineSwatch(kind)
            grid.addWidget(swatch, row + 2 + offset, 0)
            text = QLabel(label)
            grid.addWidget(text, row + 2 + offset, 2)
            self._line_swatches.append(swatch)
            self._labels.append(text)

        self.apply_theme()
        self.adjustSize()

    def contextMenuEvent(self, event) -> None:
        """Right-click offers to hide the legend; the window persists the choice."""
        menu = QMenu(self)
        menu.addAction("Hide legend", self.hide_requested.emit)
        menu.exec(event.globalPos())

    def apply_theme(self) -> None:
        """Re-tint the panel, swatches, glyphs and text for the current light/dark
        scheme. Called on construction and whenever the theme changes."""
        self.setStyleSheet(
            f"QFrame#legend {{ background-color: {theme.panel().name()}; "
            f"border: 1px solid {theme.edge().name()}; border-radius: 6px; }}"
        )
        txt = theme.text().name()
        for swatch, key in self._rows:
            fill, border = theme.node(key)
            swatch.setStyleSheet(
                f"background-color: {fill.name()}; "
                f"border: 1px solid {border.name()}; border-radius: 2px;"
            )
        for label in self._labels:
            label.setStyleSheet(f"color: {txt}; background: transparent;")
        for glyph in self._glyphs:
            glyph.update()  # re-reads theme.text_dim() on repaint
        self._separator.setStyleSheet(f"color: {theme.edge().name()};")
        for swatch in self._line_swatches:
            swatch.update()  # re-reads theme.line_pen() on repaint
