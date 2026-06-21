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
from netgrip.ui import theme

PAD = 9.0
MIN_W = 165.0
MAX_TEXT_W = 240.0

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
        painter.setPen(QPen(theme.text()))
        tm = QFontMetricsF(self._title_font)
        painter.drawText(QPointF(PAD, PAD + tm.ascent()), self._title)

        painter.setFont(self._line_font)
        painter.setPen(QPen(theme.text_dim()))
        lm = QFontMetricsF(self._line_font)
        y = PAD + self._title_h
        for line in self._lines:
            painter.drawText(QPointF(PAD, y + lm.ascent()), line)
            y += self._line_h

        self._paint_extra(painter)

    def _paint_extra(self, painter) -> None:
        pass

    def _paint_status_dot(self, painter, up: bool) -> None:
        color = theme.status(up)
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


def _vlan_summary(iface: Interface) -> str | None:
    """A bridge port's VLAN tags as `bridge vlan show` reports them, or None when
    it only carries the default untagged VLAN (the common case, not worth ink).

    A trunk reads "tagged 20,30" (plus "untagged N" if its native isn't VLAN 1);
    a plain access port reads "vlan 20".
    """
    if iface.vlan_tags:
        summary = "tagged " + ",".join(iface.vlan_tags)
        if iface.pvid not in (None, 1):
            summary += f"  untagged {iface.pvid}"
        return summary
    if iface.pvid not in (None, 1):
        return f"vlan {iface.pvid}"
    return None


def _iface_detail(iface: Interface) -> list[str]:
    # Gateway and DNS no longer live here: they belong to the per-family IP
    # group (see IpGroup), since that's the protocol that hands them out.
    lines = []
    if iface.alias:
        lines.append(iface.alias)
    if iface.mac:
        lines.append(f"{iface.mac}   mtu {iface.mtu}")
    else:
        lines.append(f"mtu {iface.mtu}")
    if iface.master:
        lines.append(f"member of {iface.master}")
    if iface.kind not in ("physical", "loopback", "vlan", "bond", "bridge"):
        lines.append(iface.kind)
    if iface.peer:
        lines.append(f"peer {iface.peer}")
    summary = _vlan_summary(iface)
    if summary:
        lines.append(summary)
    return lines


def ipgroup_detail(iface: Interface, family: int) -> list[str]:
    """The per-family settings shown in an IP group header: any DHCP/RA-assigned
    address, plus the gateway, DNS and search the same lease hands out. Static
    addresses are drawn as their own boxes inside the frame, not here."""
    lines: list[str] = []
    for addr in iface.addresses_for(family):
        if addr.dynamic:
            lines.append(f"address {addr.cidr}  (dhcp)")
    gw = iface.gateway_for(family)
    if gw:
        lines.append(f"gateway {gw.address}" + ("  (dhcp)" if gw.dynamic else ""))
    servers = iface.dns_for(family)
    if servers:
        lines.append("dns " + " ".join(servers) + ("  (dhcp)" if iface.dns_dynamic else ""))
    if iface.dns_search:
        lines.append("search " + " ".join(iface.dns_search))
    return lines


class NicNode(BaseNode):
    """A network interface card (or other plain link, incl. loopback)."""

    def __init__(self, iface: Interface):
        body, border = theme.node("loopback" if iface.kind == "loopback" else "nic")
        super().__init__(iface.name, _iface_detail(iface), body, border)
        self.iface = iface
        self.key = f"if:{iface.name}"

    def _paint_extra(self, painter) -> None:
        self._paint_status_dot(painter, self.iface.is_up)


class GroupNode(BaseNode):
    """A bond, bridge or team: several NICs acting as one link."""

    def __init__(self, iface: Interface, member_count: int):
        lines = []
        if iface.alias:
            lines.append(iface.alias)
        if iface.kind == "bond":
            lines.append(BOND_MODES.get(iface.bond_mode or "", iface.bond_mode or "bond"))
        else:
            lines.append(iface.kind)
        if iface.kind == "bridge" and iface.bridge_vlan_aware:
            lines.append("vlan-aware")
        lines.append(f"{member_count} member{'s' if member_count != 1 else ''}")
        body, border = theme.node("group")
        super().__init__(iface.name, lines, body, border)
        self.iface = iface
        self.key = f"if:{iface.name}"

    def _paint_extra(self, painter) -> None:
        self._paint_status_dot(painter, self.iface.is_up)


class VlanNode(BaseNode):
    def __init__(self, iface: Interface):
        title = f"VLAN {iface.vlan_id}"
        lines = [iface.name, f"on {iface.vlan_parent}"]
        if iface.alias:
            lines.insert(1, iface.alias)
        body, border = theme.node("vlan")
        super().__init__(title, lines, body, border)
        self.iface = iface
        self.key = f"if:{iface.name}"

    def _paint_extra(self, painter) -> None:
        self._paint_status_dot(painter, self.iface.is_up)


class DraftVlanNode(BaseNode):
    """A VLAN that does not exist yet: an id, an optional name and any pending
    addresses, drawn dashed like other drafts. Configure it, then drag it onto a
    parent link (or use its menu) to create it for real."""

    def __init__(self, draft_id: int, vlan_id: int, name: str, cidrs: list[str]):
        self.draft_id = draft_id
        self.vlan_id = vlan_id
        self.name = name
        self.cidrs = list(cidrs)
        lines = [name or "(name set on connect)"]
        lines += list(cidrs) or ["(no addresses)"]
        lines.append("drag onto a parent to create")
        body, border = theme.node("vlan")
        super().__init__(f"VLAN {vlan_id} (draft)", lines, body, border, dashed=True)
        self.key = f"draftvlan:{draft_id}"


def ip_key(parent_name: str, cidr: str) -> str:
    """Stable id for an attached address box (remembers position & name)."""
    return f"ip:{parent_name}:{cidr}"


class IpNode(BaseNode):
    """One IP address: a single CIDR of one family on one interface, or a
    draft not yet attached anywhere. A draft is a whole per-family config: it
    can also stage the gateway, DNS servers and search domains to apply on
    attach (shown as extra lines). May carry a free-form name the user gave it.
    """

    def __init__(self, family: int, cidr: str, parent_name: str | None,
                 dynamic: bool = False, draft_id: int | None = None, alias: str = "",
                 gateway: str = "", dns: list[str] | None = None,
                 dns_search: list[str] | None = None, pending_remove: bool = False):
        self.family = family
        self.cidr = cidr
        self.parent_name = parent_name
        self.draft_id = draft_id
        self.alias = alias
        # Staged per-family settings, applied when a draft is attached.
        self.gateway = gateway
        self.dns = list(dns or [])
        self.dns_search = list(dns_search or [])

        family_label = f"v{family} address"
        title = (alias or family_label) + (" (draft)" if self.is_draft else "")
        lines = [cidr + ("  (dhcp)" if dynamic else "")] if cidr else ["(no address)"]
        if self.is_draft:
            if gateway:
                lines.append(f"gateway {gateway}")
            if self.dns:
                lines.append("dns " + " ".join(self.dns))
            if self.dns_search:
                lines.append("search " + " ".join(self.dns_search))
        if alias:
            lines.append(family_label)  # keep the family visible behind the name
        if pending_remove:
            # Address still on the link at runtime; this flags the unsaved delete
            # that Save will write (the config owner would revert a runtime del).
            lines.append("→ remove on Save")
        body, border = theme.ip_node(family)
        super().__init__(title, lines, body, border, dashed=self.is_draft)
        if parent_name:
            self.key = ip_key(parent_name, cidr)
        elif draft_id is not None:
            self.key = f"draft:{draft_id}"

    @property
    def is_draft(self) -> bool:
        return self.parent_name is None

    @classmethod
    def from_address(cls, address: Address, parent_name: str, alias: str = "",
                     pending_remove: bool = False) -> IpNode:
        return cls(
            address.family, address.cidr, parent_name,
            dynamic=address.dynamic, alias=alias, pending_remove=pending_remove,
        )


def new_draft_id() -> int:
    return next(_draft_ids)


class RegionNode(QGraphicsObject):
    """A frame that groups several boxes under a shared, clickable header.

    Only the header strip is interactive: right-click it for the group's
    settings, drag it to move the whole group (frame + members) together. The
    body is inert — its area belongs to the member boxes inside (which still
    move independently and can be dragged out) and to the empty canvas behind,
    so the frame never swallows a click meant for a box or for the background.

    The frame carries no position of its own: it stays at the scene origin and
    its rectangle is recomputed to wrap its members whenever they move.
    """

    moved = Signal()
    drag_finished = Signal()

    OUTER_PAD = 12.0
    HEADER_GAP = 6.0
    INNER_GAP = 12.0

    def __init__(self, title: str, detail_lines: list[str],
                 fill: QColor, border: QColor, members: list[BaseNode]):
        super().__init__()
        # Above edges (0) so the header wins a click over the line entering it,
        # below the member boxes (1) so they stay on top inside the body.
        self.setZValue(0.5)
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
        )
        self.key: str | None = None
        self._members = list(members)
        self._fill = fill
        self._border = border

        base = QApplication.font()
        self._title_font = QFont(base)
        self._title_font.setBold(True)
        self._detail_font = QFont(base)
        self._detail_font.setPointSizeF(max(7.0, base.pointSizeF() - 1.0))
        self._title = title
        self._details = list(detail_lines)

        self._rect = QRectF()
        # Top-left where arrange() last placed the frame; used to anchor the
        # header when the group has no member boxes to wrap.
        self._origin = QPointF()
        self._drag_origin: QPointF | None = None
        self._origin_start = QPointF()
        self._member_starts: list[tuple[BaseNode, QPointF]] = []
        # Note: member moves do NOT auto-grow the frame. Dragging an address out
        # should let it leave the frame (a detach gesture), not make the frame
        # chase it. The frame is recomputed at layout time and on a header drag.
        self.refresh()

    # -- geometry / header ------------------------------------------------
    def _header_height(self) -> float:
        tm = QFontMetricsF(self._title_font)
        lm = QFontMetricsF(self._detail_font)
        return PAD + tm.height() + len(self._details) * lm.height() + PAD

    def _header_rect(self) -> QRectF:
        return QRectF(self._rect.left(), self._rect.top(),
                      self._rect.width(), self.OUTER_PAD + self._header_height())

    def _empty_width(self) -> float:
        tm = QFontMetricsF(self._title_font)
        lm = QFontMetricsF(self._detail_font)
        widest = max(
            [tm.horizontalAdvance(self._title)]
            + [lm.horizontalAdvance(d) for d in self._details],
            default=0.0,
        )
        return min(max(MIN_W, widest + 2 * PAD), MAX_TEXT_W + 2 * PAD)

    def refresh(self) -> None:
        content = QRectF()
        for member in self._members:
            content = content.united(member.sceneBoundingRect())
        if content.isNull():
            # No member boxes (e.g. a DHCP-only family): draw the header alone,
            # anchored where arrange() placed us, so the group stays visible and
            # still works as a drop target.
            new = QRectF(
                self._origin.x(), self._origin.y(),
                self._empty_width(), self.OUTER_PAD + self._header_height(),
            )
        else:
            top_reserve = self.OUTER_PAD + self._header_height() + self.HEADER_GAP
            new = content.adjusted(
                -self.OUTER_PAD, -top_reserve, self.OUTER_PAD, self.OUTER_PAD
            )
        if new != self._rect:
            self.prepareGeometryChange()
            self._rect = new
            self.update()
            self.moved.emit()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        # Only the header is "solid"; the body lets clicks fall through to the
        # member boxes or to the canvas behind. This frees the enclosed area.
        path = QPainterPath()
        if not self._rect.isNull():
            path.addRect(self._header_rect())
        return path

    def anchor(self) -> QPointF:
        return self._header_rect().center()

    def header_contains(self, scene_pos: QPointF) -> bool:
        return not self._rect.isNull() and self._header_rect().contains(scene_pos)

    def frame_rect(self) -> QRectF:
        """The whole frame in scene coordinates (empty if it has no extent)."""
        return self._rect

    def header_rect_scene(self) -> QRectF:
        """The header strip in scene coordinates — the drop target for attaching
        an address by dropping it on the group's title bar."""
        return self._header_rect() if not self._rect.isNull() else QRectF()

    # -- painting ---------------------------------------------------------
    def paint(self, painter, option, widget=None) -> None:
        if self._rect.isNull():
            return
        header = self._header_rect()
        painter.fillRect(header, self._fill)
        painter.setPen(QPen(self._border, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._rect)
        painter.drawLine(header.bottomLeft(), header.bottomRight())

        tm = QFontMetricsF(self._title_font)
        lm = QFontMetricsF(self._detail_font)
        x = header.left() + PAD
        avail = header.width() - 2 * PAD
        y = header.top() + PAD
        painter.setFont(self._title_font)
        painter.setPen(QPen(theme.text()))
        painter.drawText(
            QPointF(x, y + tm.ascent()),
            tm.elidedText(self._title, Qt.TextElideMode.ElideRight, avail),
        )
        y += tm.height()
        painter.setFont(self._detail_font)
        painter.setPen(QPen(theme.text_dim()))
        for line in self._details:
            painter.drawText(
                QPointF(x, y + lm.ascent()),
                lm.elidedText(line, Qt.TextElideMode.ElideRight, avail),
            )
            y += lm.height()

    # -- group drag (the header moves every member together) --------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.scenePos()
            self._origin_start = QPointF(self._origin)
            self._member_starts = [(m, m.pos()) for m in self._members]
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None:
            delta = event.scenePos() - self._drag_origin
            for member, start in self._member_starts:
                member.setPos(start + delta)
            if not self._members:
                self._origin = self._origin_start + delta
            # A whole-group drag should carry the frame with it, so refresh here
            # (unlike a single member's drag, which must not grow the frame).
            self.refresh()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        dragged = (
            self._drag_origin is not None
            and (event.scenePos() - self._drag_origin).manhattanLength() > 4
        )
        self._drag_origin = None
        self._member_starts = []
        if dragged:
            self.drag_finished.emit()
        else:
            super().mouseReleaseEvent(event)

    # -- auto-layout ------------------------------------------------------
    def block_width(self) -> float:
        widest = max((m.boundingRect().width() for m in self._members), default=0.0)
        return widest + 2 * self.OUTER_PAD

    def block_height(self) -> float:
        """The height this group will span once arranged, computed without
        moving anything — so the layout engine can size the column before
        :meth:`arrange` actually places the members. Mirrors arrange()'s sum."""
        if not self._members:
            return self.OUTER_PAD + self._header_height()
        h = self.OUTER_PAD + self._header_height() + self.HEADER_GAP
        for member in self._members:
            h += member.boundingRect().height() + self.INNER_GAP
        return h - self.INNER_GAP + self.OUTER_PAD

    def arrange(self, left: float, top: float) -> float:
        """Stack the members vertically under the header, with the frame's left
        edge at ``left`` and top at ``top``; return the height the group spans."""
        self._origin = QPointF(left, top)
        member_x = left + self.OUTER_PAD
        cur = top + self.OUTER_PAD + self._header_height() + self.HEADER_GAP
        for member in self._members:
            member.setPos(member_x, cur)
            cur += member.boundingRect().height() + self.INNER_GAP
        self.refresh()
        if not self._members:
            return self.OUTER_PAD + self._header_height()
        return (cur - self.INNER_GAP + self.OUTER_PAD) - top


class IpGroup(RegionNode):
    """All addresses of one family on one interface, framed together with the
    gateway, DNS and search that family's lease hands out. The drop target for
    attaching another address to this interface."""

    def __init__(self, iface: Interface, family: int, members: list[BaseNode],
                 pending_dhcp: bool = False):
        fill, border = theme.region(family)
        detail = ipgroup_detail(iface, family)
        if pending_dhcp:
            # The family still holds its static address at runtime; this flags
            # the unsaved switch to DHCP that Save will write (M5).
            detail = [*detail, "→ DHCP on Save"]
        super().__init__(f"IPv{family}", detail, fill, border, members)
        self.iface = iface
        self.family = family
        self.key = f"ipgroup:{iface.name}:{family}"


class SystemDns(RegionNode):
    """Host-wide resolvers (resolv.conf), each tagged with where it comes from,
    plus any manual extras. Drawn as a frame around the whole diagram with an
    interactive title bar: DNS is system-wide, so the frame says "this applies
    to everything" by enclosing it.

    Like every :class:`RegionNode`, only the title bar is solid — the framed
    body is transparent to clicks (see :meth:`RegionNode.shape`), so the frame
    never swallows a gesture meant for a box or the canvas behind it. It is
    pinned (not draggable): :meth:`wrap` re-fits it around the diagram on every
    layout. Right-click the title bar to edit manual resolvers."""

    # Breathing room between the frame border and the diagram it encloses.
    FRAME_PAD = 16.0

    def __init__(self, servers: list[str], search: list[str],
                 manual: list[str], origin):
        self.servers = list(servers)
        self.search = list(search)
        self.manual = list(manual)

        shown = list(servers)
        for extra in manual:
            if extra not in shown:
                shown.append(extra)
        lines = [f"{s}   ← {origin(s)}" for s in shown] or ["(no resolvers)"]
        if search:
            lines.append("search " + " ".join(search))
        lines.append("+ add resolver…")
        fill, border = theme.node("dns")
        super().__init__("System DNS", lines, fill, border, members=[])

    def top_reserve(self) -> float:
        """Vertical space the title bar (plus padding) needs above the diagram,
        so the canvas can start the tree low enough to sit inside the frame."""
        return self.OUTER_PAD + self._header_height() + self.HEADER_GAP + self.FRAME_PAD

    def wrap(self, content: QRectF) -> None:
        """Fit the frame around ``content`` (the whole diagram), title bar on
        top. With nothing to wrap, fall back to the header alone at the origin."""
        if content.isNull():
            self._origin = QPointF(self.OUTER_PAD, self.OUTER_PAD)
            new = QRectF(self.OUTER_PAD, self.OUTER_PAD, self._empty_width(),
                         self.OUTER_PAD + self._header_height())
        else:
            new = content.adjusted(
                -self.FRAME_PAD, -self.top_reserve(), self.FRAME_PAD, self.FRAME_PAD
            )
        if new != self._rect:
            self.prepareGeometryChange()
            self._rect = new
            self.update()

    def mousePressEvent(self, event) -> None:
        # Pinned frame: the title bar is a right-click target, never a drag
        # handle. Swallow the left press so the frame stays wrapped and the
        # view doesn't start a rubber-band selection from the title bar.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
        else:
            super().mousePressEvent(event)


class Edge(QGraphicsPathItem):
    """A straight line between the centers of two nodes, drawn under them."""

    def __init__(self, a: BaseNode, b: BaseNode):
        super().__init__()
        self.setZValue(0)
        self.setPen(QPen(theme.edge(), 1.4))
        self._a = a
        self._b = b
        a.moved.connect(self.refresh)
        b.moved.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        path = QPainterPath(self._a.anchor())
        path.lineTo(self._b.anchor())
        self.setPath(path)
