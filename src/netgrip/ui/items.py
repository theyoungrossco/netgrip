"""Canvas items: flat rectangular boxes for NICs, groups, VLANs and IP
configs, joined by plain straight lines.
"""

from __future__ import annotations

import itertools

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsObject, QGraphicsPathItem

from netgrip.core.actions import BOND_MODES
from netgrip.core.model import Address, Interface

PAD = 9.0
MIN_W = 165.0
MAX_TEXT_W = 240.0

TEXT = QColor("#202830")
TEXT_DIM = QColor("#4d5a66")
EDGE_COLOR = QColor("#8a949e")

_draft_ids = itertools.count(1)


class BaseNode(QGraphicsObject):
    """A flat rectangle with a bold title line and smaller detail lines."""

    moved = Signal()
    drag_finished = Signal()

    def __init__(self, title: str, lines: list[str], body: QColor, border: QColor,
                 dashed: bool = False):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(1)

        self.key: str | None = None  # stable id for remembering positions
        self._body = body
        self._border = border
        self._dashed = dashed
        self._press_pos: QPointF | None = None

        base = QApplication.font()
        self._title_font = QFont(base)
        self._title_font.setBold(True)
        self._line_font = QFont(base)
        self._line_font.setPointSizeF(max(7.0, base.pointSizeF() - 1.5))

        tm = QFontMetricsF(self._title_font)
        lm = QFontMetricsF(self._line_font)
        self._title = tm.elidedText(title, Qt.TextElideMode.ElideRight, MAX_TEXT_W)
        self._lines = [lm.elidedText(ln, Qt.TextElideMode.ElideRight, MAX_TEXT_W) for ln in lines]
        self._title_h = tm.height()
        self._line_h = lm.height()

        widest = max(
            [tm.horizontalAdvance(self._title) + 16]  # room for the status dot
            + [lm.horizontalAdvance(ln) for ln in self._lines],
            default=0,
        )
        self._w = min(max(MIN_W, widest + 2 * PAD), MAX_TEXT_W + 2 * PAD)
        self._h = PAD + self._title_h + (len(self._lines) * self._line_h) + PAD

    # -- geometry ---------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def anchor(self) -> QPointF:
        return self.sceneBoundingRect().center()

    # -- painting ---------------------------------------------------------
    def paint(self, painter, option, widget=None) -> None:
        rect = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(self._border, 2.0 if self.isSelected() else 1.0)
        if self._dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.fillRect(rect, self._body)
        painter.setPen(pen)
        painter.drawRect(rect)

        painter.setFont(self._title_font)
        painter.setPen(QPen(TEXT))
        tm = QFontMetricsF(self._title_font)
        painter.drawText(QPointF(PAD, PAD + tm.ascent()), self._title)

        painter.setFont(self._line_font)
        painter.setPen(QPen(TEXT_DIM))
        lm = QFontMetricsF(self._line_font)
        y = PAD + self._title_h
        for line in self._lines:
            painter.drawText(QPointF(PAD, y + lm.ascent()), line)
            y += self._line_h

        self._paint_extra(painter)

    def _paint_extra(self, painter) -> None:
        pass

    def _paint_status_dot(self, painter, up: bool) -> None:
        color = QColor("#2e9e4f") if up else QColor("#c34a3a")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        r = 4.0
        painter.drawEllipse(QPointF(self._w - PAD - r, PAD + r + 1), r, r)

    # -- interaction ------------------------------------------------------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        self._press_pos = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self._press_pos is not None and (self.pos() - self._press_pos).manhattanLength() > 4:
            self.drag_finished.emit()
        self._press_pos = None


def _iface_detail(iface: Interface) -> list[str]:
    lines = []
    if iface.mac:
        lines.append(f"{iface.mac}   mtu {iface.mtu}")
    else:
        lines.append(f"mtu {iface.mtu}")
    if iface.master:
        lines.append(f"member of {iface.master}")
    if iface.kind not in ("physical", "loopback", "vlan", "bond", "bridge"):
        lines.append(iface.kind)
    return lines


class NicNode(BaseNode):
    """A network interface card (or other plain link, incl. loopback)."""

    def __init__(self, iface: Interface):
        body = QColor("#e7eef5") if iface.kind != "loopback" else QColor("#ececec")
        border = QColor("#46729c") if iface.kind != "loopback" else QColor("#9a9a9a")
        super().__init__(iface.name, _iface_detail(iface), body, border)
        self.iface = iface
        self.key = f"if:{iface.name}"

    def _paint_extra(self, painter) -> None:
        self._paint_status_dot(painter, self.iface.is_up)


class GroupNode(BaseNode):
    """A bond, bridge or team: several NICs acting as one link."""

    def __init__(self, iface: Interface, member_count: int):
        lines = []
        if iface.kind == "bond":
            lines.append(BOND_MODES.get(iface.bond_mode or "", iface.bond_mode or "bond"))
        else:
            lines.append(iface.kind)
        lines.append(f"{member_count} member{'s' if member_count != 1 else ''}")
        super().__init__(iface.name, lines, QColor("#f6e8d4"), QColor("#a8742f"))
        self.iface = iface
        self.key = f"if:{iface.name}"

    def _paint_extra(self, painter) -> None:
        self._paint_status_dot(painter, self.iface.is_up)


class VlanNode(BaseNode):
    def __init__(self, iface: Interface):
        title = f"VLAN {iface.vlan_id}"
        lines = [iface.name, f"on {iface.vlan_parent}"]
        super().__init__(title, lines, QColor("#dcefec"), QColor("#2f8a80"))
        self.iface = iface
        self.key = f"if:{iface.name}"

    def _paint_extra(self, painter) -> None:
        self._paint_status_dot(painter, self.iface.is_up)


class IpNode(BaseNode):
    """An IP configuration: all addresses of one family on one interface,
    or a draft not yet attached anywhere."""

    def __init__(self, family: int, cidrs: list[str], parent_name: str | None,
                 dynamic_cidrs: set[str] | None = None, draft_id: int | None = None):
        self.family = family
        self.cidrs = list(cidrs)
        self.parent_name = parent_name
        self.draft_id = draft_id
        dynamic_cidrs = dynamic_cidrs or set()

        title = f"IPv{family}" + (" (draft)" if self.is_draft else "")
        lines = [c + ("  (dhcp)" if c in dynamic_cidrs else "") for c in self.cidrs]
        if not lines:
            lines = ["(no addresses)"]
        if family == 4:
            body, border = QColor("#e0f0dc"), QColor("#3f8a44")
        else:
            body, border = QColor("#e9e2f6"), QColor("#6d51a8")
        super().__init__(title, lines, body, border, dashed=self.is_draft)
        if parent_name:
            self.key = f"ip{family}:{parent_name}"
        elif draft_id is not None:
            self.key = f"draft:{draft_id}"

    @property
    def is_draft(self) -> bool:
        return self.parent_name is None

    @classmethod
    def from_addresses(cls, family: int, addresses: list[Address], parent_name: str) -> IpNode:
        return cls(
            family,
            [a.cidr for a in addresses],
            parent_name,
            dynamic_cidrs={a.cidr for a in addresses if a.dynamic},
        )


def new_draft_id() -> int:
    return next(_draft_ids)


class Edge(QGraphicsPathItem):
    """A straight line between the centers of two nodes, drawn under them."""

    def __init__(self, a: BaseNode, b: BaseNode):
        super().__init__()
        self.setZValue(0)
        self.setPen(QPen(EDGE_COLOR, 1.4))
        self._a = a
        self._b = b
        a.moved.connect(self.refresh)
        b.moved.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        path = QPainterPath(self._a.anchor())
        path.lineTo(self._b.anchor())
        self.setPath(path)
