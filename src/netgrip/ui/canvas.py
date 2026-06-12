"""The canvas: lays out interface boxes and connector lines, and detects
drops of one box onto another.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

from netgrip.core.model import HostState
from netgrip.ui.items import BaseNode, Edge, GroupNode, IpNode, NicNode, VlanNode, new_draft_id

MARGIN = 30.0
COL_W = 270.0
V_GAP = 22.0
# A drop only counts if the dragged box overlaps the target by this share
# of its own area; less than that is treated as repositioning.
MIN_OVERLAP = 0.35


class Canvas(QGraphicsView):
    node_menu_requested = Signal(object, QPoint)  # node, global pos
    canvas_menu_requested = Signal(QPoint, QPointF)  # global pos, scene pos
    ip_dropped = Signal(object, object, bool)  # IpNode, target node, clone?
    nic_dropped = Signal(object, object)  # NicNode, target NicNode/GroupNode
    vlan_dropped = Signal(object, object)  # VlanNode, target node

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#fafafa"))

        self._state: HostState | None = None
        self._show_loopback = False
        self._positions: dict[str, QPointF] = {}  # remembered node positions
        self._drafts: list[dict] = []  # {id, family, cidrs, pos}

    # ------------------------------------------------------------------ #
    # population & layout
    # ------------------------------------------------------------------ #
    def populate(self, state: HostState | None, show_loopback: bool | None = None) -> None:
        if show_loopback is not None:
            self._show_loopback = show_loopback
        self._state = state
        scene = self.scene()
        scene.clear()
        if state is None:
            return

        shown = [
            i for i in state.interfaces
            if self._show_loopback or i.kind != "loopback"
        ]
        shown_names = {i.name for i in shown}

        # Build one node per interface plus one IP box per family in use.
        if_nodes: dict[str, BaseNode] = {}
        for iface in shown:
            if iface.is_group:
                node: BaseNode = GroupNode(iface, len(state.members_of(iface.name)))
            elif iface.kind == "vlan":
                node = VlanNode(iface)
            else:
                node = NicNode(iface)
            if_nodes[iface.name] = node

        ip_nodes: list[IpNode] = []
        for iface in shown:
            for family in (4, 6):
                addrs = iface.addresses_for(family)
                if addrs:
                    ip_nodes.append(IpNode.from_addresses(family, addrs, iface.name))

        draft_nodes = [
            IpNode(d["family"], d["cidrs"], None, draft_id=d["id"]) for d in self._drafts
        ]

        # Parent->children map drives both the edges and the tree layout.
        children: dict[str, list[BaseNode]] = {name: [] for name in if_nodes}
        roots: list[BaseNode] = []
        for iface in shown:
            node = if_nodes[iface.name]
            if iface.kind == "vlan" and iface.vlan_parent in shown_names:
                children[iface.vlan_parent].append(node)
            elif iface.is_group:
                members = [m.name for m in state.members_of(iface.name) if m.name in shown_names]
                if members:
                    children[members[0]].append(node)  # layout under first member
                else:
                    roots.append(node)
            elif iface.master is None or iface.master not in shown_names:
                roots.append(node)
            else:
                roots.append(node)  # enslaved NICs still sit in the first column
        for ip_node in ip_nodes:
            children[ip_node.parent_name].append(ip_node)

        # Order: physical NICs first, groups without members, loopback last.
        def root_rank(n: BaseNode) -> tuple:
            iface = n.iface  # all roots are interface nodes
            return (iface.kind == "loopback", iface.kind != "physical", iface.name)

        roots.sort(key=root_rank)

        for node in [*if_nodes.values(), *ip_nodes, *draft_nodes]:
            scene.addItem(node)
            node.drag_finished.connect(self._make_drop_handler(node))
            if node.key:
                node.moved.connect(self._make_position_saver(node))

        # Edges: vlan->parent, member->group, ip->owner.
        for iface in shown:
            node = if_nodes[iface.name]
            if iface.kind == "vlan" and iface.vlan_parent in if_nodes:
                scene.addItem(Edge(if_nodes[iface.vlan_parent], node))
            if iface.master and iface.master in if_nodes:
                scene.addItem(Edge(node, if_nodes[iface.master]))
        for ip_node in ip_nodes:
            scene.addItem(Edge(if_nodes[ip_node.parent_name], ip_node))

        self._layout_tree(roots, children, if_nodes)

        for draft, node in zip(self._drafts, draft_nodes, strict=True):
            node.setPos(draft["pos"])
            node.moved.connect(self._make_draft_position_saver(draft, node))

        # Remembered positions win over the automatic layout.
        for node in [*if_nodes.values(), *ip_nodes]:
            if node.key in self._positions:
                node.setPos(self._positions[node.key])

        rect = scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        scene.setSceneRect(rect)

    def _layout_tree(self, roots, children, if_nodes) -> None:
        y = MARGIN

        def place(node: BaseNode, depth: int, top: float) -> float:
            """Position node and its subtree; return the subtree height."""
            kids = children.get(getattr(node, "iface", None) and node.iface.name, [])
            x = MARGIN + depth * COL_W
            if not kids:
                node.setPos(x, top)
                return node.boundingRect().height()
            cursor = top
            for kid in kids:
                cursor += place(kid, depth + 1, cursor) + V_GAP
            block = cursor - V_GAP - top
            node.setPos(x, top + max(0.0, (block - node.boundingRect().height()) / 2))
            return max(block, node.boundingRect().height())

        for root in roots:
            y += place(root, 0, y) + V_GAP

    def auto_layout(self) -> None:
        self._positions.clear()
        self.populate(self._state)

    # ------------------------------------------------------------------ #
    # drafts (IP configs not attached to any interface yet)
    # ------------------------------------------------------------------ #
    def add_draft(self, family: int, cidrs: list[str], scene_pos: QPointF) -> None:
        self._drafts.append(
            {"id": new_draft_id(), "family": family, "cidrs": list(cidrs), "pos": scene_pos}
        )
        self.populate(self._state)

    def update_draft(self, draft_id: int, cidrs: list[str]) -> None:
        for d in self._drafts:
            if d["id"] == draft_id:
                d["cidrs"] = list(cidrs)
        self.populate(self._state)

    def remove_draft(self, draft_id: int) -> None:
        self._drafts = [d for d in self._drafts if d["id"] != draft_id]
        self.populate(self._state)

    # ------------------------------------------------------------------ #
    # drop detection
    # ------------------------------------------------------------------ #
    def _make_drop_handler(self, node: BaseNode):
        return lambda: self._node_dropped(node)

    def _make_position_saver(self, node: BaseNode):
        return lambda: self._positions.__setitem__(node.key, node.pos())

    def _make_draft_position_saver(self, draft: dict, node: BaseNode):
        return lambda: draft.__setitem__("pos", node.pos())

    def _node_dropped(self, node: BaseNode) -> None:
        if isinstance(node, IpNode):
            target = self._drop_target(node, (NicNode, GroupNode, VlanNode))
            if target and target.iface.name != node.parent_name:
                clone = bool(
                    QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
                )
                self.ip_dropped.emit(node, target, clone)
        elif isinstance(node, VlanNode):
            target = self._drop_target(node, (NicNode, GroupNode))
            if target and target.iface.name != node.iface.vlan_parent:
                self.vlan_dropped.emit(node, target)
        elif isinstance(node, NicNode) and node.iface.kind == "physical":
            target = self._drop_target(node, (NicNode, GroupNode))
            if isinstance(target, GroupNode):
                self.nic_dropped.emit(node, target)
            elif (
                isinstance(target, NicNode)
                and target.iface.kind == "physical"
                and target.iface.name != node.iface.name
            ):
                self.nic_dropped.emit(node, target)

    def _drop_target(self, node: BaseNode, kinds: tuple) -> BaseNode | None:
        own_rect = node.sceneBoundingRect()
        own_area = own_rect.width() * own_rect.height()
        best, best_area = None, 0.0
        for item in node.collidingItems():
            if not isinstance(item, kinds):
                continue
            overlap = item.sceneBoundingRect().intersected(own_rect)
            area = overlap.width() * overlap.height()
            if area > best_area:
                best, best_area = item, area
        if best is not None and own_area > 0 and best_area / own_area >= MIN_OVERLAP:
            return best
        return None

    # ------------------------------------------------------------------ #
    # menus & zoom
    # ------------------------------------------------------------------ #
    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, BaseNode):
            item = item.parentItem()
        if isinstance(item, BaseNode):
            self.node_menu_requested.emit(item, event.globalPos())
        else:
            self.canvas_menu_requested.emit(event.globalPos(), self.mapToScene(event.pos()))
        event.accept()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def fit_all(self) -> None:
        rect: QRectF = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(
                rect.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            # Never zoom in past 1:1; tiny scenes should not become huge boxes.
            if self.transform().m11() > 1.0:
                self.resetTransform()
