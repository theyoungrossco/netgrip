"""The legend: a small floating colour key for the canvas.

Pinned to the canvas's top-left corner (mirroring the Save button top-right),
toggled from the View menu and persisted in QSettings. Every colour is pulled
live from ``theme.py`` and re-applied on a light/dark switch, so the swatches
always match the boxes they explain. Rows come from ``theme.LEGEND_CATEGORIES``,
the single table the Definitions page (workstream E) shares.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel

from netgrip.ui import theme


class Legend(QFrame):
    """A floating key mapping each box colour to its category."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("legend")
        self._rows: list[tuple[QLabel, str]] = []  # (swatch, colour key)
        self._labels: list[QLabel] = []

        grid = QGridLayout(self)
        grid.setContentsMargins(11, 9, 13, 9)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(5)
        for row, (label, key, _hint) in enumerate(theme.LEGEND_CATEGORIES):
            swatch = QLabel()
            swatch.setFixedSize(18, 12)
            text = QLabel(label)
            grid.addWidget(swatch, row, 0)
            grid.addWidget(text, row, 1)
            self._rows.append((swatch, key))
            self._labels.append(text)

        self.apply_theme()
        self.adjustSize()

    def apply_theme(self) -> None:
        """Re-tint the panel, swatches and text for the current light/dark scheme.
        Called on construction and whenever the theme changes."""
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
