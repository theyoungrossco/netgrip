"""Theme: one place that decides every colour the canvas paints.

"Flat" in netgrip means the *network view* is flat — squares joined by straight
lines — not that the app should be drab. Colours follow the OS theme: this
module resolves a light/dark scheme (from the user's choice, the platform, or
the palette) and hands out matching colours, so the canvas sits naturally on a
light or a dark desktop.

UI-layer module: importing Qt here is fine (unlike ``core``).
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Resolved once via apply()/scheme(); "light" or "dark".
_scheme: str | None = None

# Per-scheme palette of named colours. Node entries are (fill, border).
_LIGHT = {
    "background": "#f5f6f8",
    # A raised surface for widgets floating over the canvas (the legend), a
    # touch off the background so the panel reads as a separate layer.
    "panel": "#ffffff",
    "text": "#1b2430",
    "text_dim": "#5b6672",
    "edge": "#aab2bb",
    "error": "#c0392b",
    "up": "#2e9e4f",
    "down": "#c34a3a",
    "nic": ("#e9eff6", "#4a77a3"),
    "loopback": ("#eceef0", "#9aa3ab"),
    "group": ("#f6ebd8", "#b07f33"),
    "vlan": ("#dcefec", "#2f8a80"),
    "ip4": ("#e2f1dd", "#3f8a44"),
    "ip6": ("#ece4f7", "#6d51a8"),
    # Region frames that group an interface's addresses of one family: a faint
    # header tint over the same border as the family's address boxes.
    "region4": ("#eaf4e5", "#3f8a44"),
    "region6": ("#f1ebfa", "#6d51a8"),
    "dns": ("#eceef0", "#7a828a"),
}
_DARK = {
    "background": "#1e2228",
    "panel": "#262b32",
    "text": "#e6e9ee",
    "text_dim": "#9aa4b0",
    "edge": "#525a63",
    "error": "#e06a5a",
    "up": "#46c46e",
    "down": "#e06a5a",
    "nic": ("#26313d", "#5b8cb5"),
    "loopback": ("#2a2e34", "#6b747d"),
    "group": ("#36301f", "#c79a52"),
    "vlan": ("#1f3431", "#4fb3a6"),
    "ip4": ("#1f3020", "#5fae5f"),
    "ip6": ("#2a2440", "#9b86cf"),
    "region4": ("#1a2618", "#5fae5f"),
    "region6": ("#221d33", "#9b86cf"),
    "dns": ("#2a2e34", "#828b94"),
}

# Legend / glossary categories in display order: (label, colour key, hint). The
# colour key indexes node(); the hint is the one-line gloss the legend and the
# Definitions page (workstream E) share, so the two can never drift apart.
LEGEND_CATEGORIES = [
    ("Physical interface", "nic",
     "A real hardware port — Wired (Ethernet) or Wireless (Wi-Fi)."),
    ("Group (bond / bridge / team)", "group",
     "Several interfaces joined into one logical link or L2 switch."),
    ("Virtual interface", "vlan",
     "A VLAN today: a tagged subinterface of one parent (id 1–4094)."),
    ("IPv4 config", "ip4",
     "An IPv4 address with its gateway and DNS on an interface."),
    ("IPv6 config", "ip6",
     "An IPv6 address with its gateway and DNS on an interface."),
    ("System DNS", "dns",
     "The host's name resolvers and DNS search domains."),
    ("Loopback", "loopback",
     "The host-internal interface (127.0.0.1 / ::1)."),
]


def _detect_scheme() -> str:
    """Pick a scheme from an env override, the platform, then the palette."""
    override = os.environ.get("NETGRIP_THEME")
    if override in ("light", "dark"):
        return override
    app = QApplication.instance()
    if app is not None:
        try:
            hint = app.styleHints().colorScheme()
            if hint == Qt.ColorScheme.Dark:
                return "dark"
            if hint == Qt.ColorScheme.Light:
                return "light"
        except (AttributeError, RuntimeError):
            pass  # older Qt without colorScheme(); fall back to the palette
        if app.palette().window().color().lightness() < 128:
            return "dark"
    return "light"


def scheme() -> str:
    global _scheme
    if _scheme is None:
        _scheme = _detect_scheme()
    return _scheme


def is_dark() -> bool:
    return scheme() == "dark"


def _table() -> dict:
    return _DARK if is_dark() else _LIGHT


# -- named colours ---------------------------------------------------------
def background() -> QColor:
    return QColor(_table()["background"])


def panel() -> QColor:
    """A raised surface colour for widgets floating over the canvas (the legend)."""
    return QColor(_table()["panel"])


def text() -> QColor:
    return QColor(_table()["text"])


def text_dim() -> QColor:
    return QColor(_table()["text_dim"])


def edge() -> QColor:
    return QColor(_table()["edge"])


def error() -> QColor:
    return QColor(_table()["error"])


def status(up: bool) -> QColor:
    return QColor(_table()["up" if up else "down"])


def node(kind: str) -> tuple[QColor, QColor]:
    """Return (fill, border) for a node kind: nic/loopback/group/vlan/dns."""
    fill, border = _table()[kind]
    return QColor(fill), QColor(border)


def ip_node(family: int) -> tuple[QColor, QColor]:
    fill, border = _table()["ip4" if family == 4 else "ip6"]
    return QColor(fill), QColor(border)


def region(family: int) -> tuple[QColor, QColor]:
    """(header fill, border) for an IPv4/IPv6 group frame."""
    fill, border = _table()["region4" if family == 4 else "region6"]
    return QColor(fill), QColor(border)


def save_button_style() -> str:
    """Stylesheet for the floating *Save* affordance on the canvas.

    Deliberately loud — it appears only when changes are pending and persisting
    them is a real, reboot-affecting commit, so it reads as an attention button
    (the warning/error red) rather than a quiet toolbar entry. Palette-aware so
    it stays legible in light and dark, keeping every colour decision here per
    the project's theme rule."""
    base = QColor(_table()["error"])
    return f"""
        QPushButton {{
            background-color: {base.name()};
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 9px 18px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background-color: {base.lighter(112).name()}; }}
        QPushButton:pressed {{ background-color: {base.darker(115).name()}; }}
    """


def help_button_style() -> str:
    """Stylesheet for the toolbar *Help* (``?``) button: a bold question mark in
    a circular outline so it stands out as the help affordance rather than a
    faint glyph. Palette-aware (border/text from the theme) per the colour rule;
    the menu-indicator arrow is suppressed so the ``?`` stays centred."""
    border = QColor(_table()["text_dim"])
    txt = QColor(_table()["text"])
    return f"""
        QToolButton {{
            color: {txt.name()};
            font-weight: bold;
            font-size: 15px;
            border: 1.5px solid {border.name()};
            border-radius: 13px;
            min-width: 26px;
            max-width: 26px;
            min-height: 26px;
            max-height: 26px;
        }}
        QToolButton:hover {{ border-color: {txt.name()}; }}
        QToolButton::menu-indicator {{ image: none; width: 0px; }}
    """


# -- application of the scheme --------------------------------------------
def apply_theme(app: QApplication, mode: str = "system") -> str:
    """Resolve ``mode`` (system|light|dark), set the app palette, return scheme.

    For light we keep the platform's own palette (so a themed desktop shows
    through). For dark we install a neutral dark palette, which also gives the
    window chrome — menus, toolbar, dialogs — a consistent dark look even when
    no Qt platform theme is present.
    """
    global _scheme
    if mode in ("light", "dark"):
        _scheme = mode
    else:
        _scheme = _detect_scheme()
    if _scheme == "dark":
        app.setPalette(_dark_palette())
    else:
        app.setPalette(app.style().standardPalette())
    return _scheme


def _dark_palette() -> QPalette:
    t = _DARK
    base = QColor(t["background"])
    panel = QColor(t["panel"])
    txt = QColor(t["text"])
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, base)
    pal.setColor(QPalette.ColorRole.WindowText, txt)
    pal.setColor(QPalette.ColorRole.Base, QColor("#23272e"))
    pal.setColor(QPalette.ColorRole.AlternateBase, panel)
    pal.setColor(QPalette.ColorRole.ToolTipBase, panel)
    pal.setColor(QPalette.ColorRole.ToolTipText, txt)
    pal.setColor(QPalette.ColorRole.Text, txt)
    pal.setColor(QPalette.ColorRole.Button, panel)
    pal.setColor(QPalette.ColorRole.ButtonText, txt)
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#3d6ea5"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["text_dim"]))
    disabled = QColor(t["text_dim"])
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return pal
