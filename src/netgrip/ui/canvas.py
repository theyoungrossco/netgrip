"""The canvas: lays out interface boxes and connector lines, and detects
drops of one box onto another.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

from netgrip.core import layout, store
from netgrip.core.model import HostState, ip_family
from netgrip.ui import theme
from netgrip.ui.items import (
    BaseNode,
    ContainerNode,
    DraftVlanNode,
    Edge,
    GroupNode,
    IpGroup,
    IpNode,
    NicNode,
    RegionNode,
    RouteEdge,
    SystemDns,
    VlanNode,
    new_draft_id,
)


def _alias_key(family: int, cidr: str) -> str:
    """Key under which a user-given box name is stored. Keyed by address (not
    by interface) so a name follows its address as it is moved or detached."""
    return f"{family}:{cidr}"

MARGIN = 30.0
COL_GAP = 40.0  # horizontal space between layout columns
V_GAP = 22.0
# A drop only counts if the dragged box overlaps the target by this share
# of its own area; less than that is treated as repositioning.
MIN_OVERLAP = 0.35


class Canvas(QGraphicsView):
    node_menu_requested = Signal(object, QPoint)  # node, global pos
    region_menu_requested = Signal(object, QPoint)  # RegionNode (IpGroup), global pos
    canvas_menu_requested = Signal(QPoint, QPointF)  # global pos, scene pos
    ip_dropped = Signal(object, object, bool)  # IpNode, target (NIC/group/IpGroup), clone?
    ip_detached = Signal(object)  # IpNode dragged clear of its own group
    nic_dropped = Signal(object, object)  # NicNode, target NicNode/GroupNode
    vlan_dropped = Signal(object, object)  # VlanNode, target node
    draft_vlan_dropped = Signal(object, object)  # DraftVlanNode, parent link node

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(theme.background())

        self._state: HostState | None = None
        self._show_loopback = False
        self._hide_offline = False
        # A container's L3 lines (RouteEdge), each toggleable from the View menu:
        # its published-port forwards and its outbound default-route line.
        self._show_forwards = True
        self._show_egress = True
        self._host_label: str | None = None  # whose persisted state is loaded
        self._positions: dict[str, QPointF] = {}  # remembered node positions
        # While the auto-layout places boxes, suppress the position saver so its
        # setPos() calls don't overwrite the user's remembered positions: the
        # store must hold only genuine user placements, never the auto guesses.
        self._laying_out = False
        self._drafts: list[dict] = []  # {id, family, cidr, pos}
        self._draft_vlans: list[dict] = []  # {id, vlan_id, name, cidrs, pos}
        self._aliases: dict[str, str] = {}  # _alias_key() -> user box name
        self._manual_dns: list[str] = []  # user-added host-wide resolvers
        # Widgets pinned to viewport corners (the floating Save button top-right,
        # the Legend top-left), floating above the scene and unaffected by
        # scroll/zoom. Repositioned on resize; see set_corner_widget(). Keyed by
        # corner so each corner holds at most one widget.
        self._corner_widgets: dict[str, object] = {}

    # ------------------------------------------------------------------ #
    # corner overlays (viewport-pinned widgets, e.g. Save button, Legend)
    # ------------------------------------------------------------------ #
    def set_corner_widget(self, widget, corner: str = "top-right") -> None:
        """Pin ``widget`` to a viewport corner ("top-right" or "top-left"). It is
        reparented onto the viewport so it floats over the diagram and ignores
        pan/zoom."""
        self._corner_widgets[corner] = widget
        widget.setParent(self.viewport())
        self.position_corner_widget(corner)

    def position_corner_widget(self, corner: str | None = None) -> None:
        """Re-pin corner widgets. With no ``corner``, repositions every pinned
        widget (used on resize); with one, just that corner (e.g. after the Save
        button's text grows). Top-left sits at the margin; top-right tracks the
        viewport's right edge so it stays anchored as the window resizes."""
        margin = 16
        for c in ([corner] if corner is not None else list(self._corner_widgets)):
            widget = self._corner_widgets.get(c)
            if widget is None:
                continue
            if c == "top-left":
                widget.move(margin, margin)
            else:  # top-right
                widget.move(self.viewport().width() - widget.width() - margin, margin)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.position_corner_widget()

    # ------------------------------------------------------------------ #
    # population & layout
    # ------------------------------------------------------------------ #
    def populate(self, state: HostState | None, show_loopback: bool | None = None,
                 hide_offline: bool | None = None, show_forwards: bool | None = None,
                 show_egress: bool | None = None) -> None:
        if show_loopback is not None:
            self._show_loopback = show_loopback
        if hide_offline is not None:
            self._hide_offline = hide_offline
        if show_forwards is not None:
            self._show_forwards = show_forwards
        if show_egress is not None:
            self._show_egress = show_egress
        self._state = state
        self.setBackgroundBrush(theme.background())  # follow theme changes
        if state is not None and state.label != self._host_label:
            self._load_state_for(state.label)
        if state is not None:
            state.manual_dns = list(self._manual_dns)  # for resolver provenance
        scene = self.scene()
        scene.clear()
        if state is None:
            return

        # A docker container's host-side veth is just the cable to its container;
        # drawn on its own it's an anonymous box, one per container. Fold it away
        # and let the container box (attached to the same bridge) stand for it, so
        # there's one box per container rather than a veth box beside it.
        docker_bridges = {n.bridge for n in state.docker_networks if n.bridge}

        # Loopback has its own toggle (show_loopback); every other interface can
        # be filtered out when down via hide_offline. Drafts are added later, so
        # they're never affected by either.
        shown = [
            i for i in state.interfaces
            if (self._show_loopback or i.kind != "loopback")
            and not (self._hide_offline and not i.is_up and i.kind != "loopback")
            and not (i.kind == "veth" and i.master in docker_bridges)
        ]
        shown_names = {i.name for i in shown}

        # One node per interface.
        if_nodes: dict[str, BaseNode] = {}
        for iface in shown:
            if iface.is_group:
                node: BaseNode = GroupNode(iface, len(state.members_of(iface.name)))
            elif iface.kind == "vlan":
                node = VlanNode(iface)
            else:
                node = NicNode(iface)
            if_nodes[iface.name] = node

        # Each interface's addresses are grouped, per family, into an IpGroup
        # region that also carries that family's gateway / DNS / search.
        ip_nodes: list[IpNode] = []  # every member box (for save & positions)
        ip_groups: list[IpGroup] = []
        groups_by_iface: dict[str, list[IpGroup]] = {name: [] for name in if_nodes}
        pending_dhcp = getattr(state, "dhcp_pending", set())
        removed_pending = getattr(state, "removed_pending", set())
        dns_off_pending = getattr(state, "dns_off_pending", set())
        for iface in shown:
            # A family pending a switch to DHCP keeps its box even if its last
            # static address has gone, so the user can see (and Save) the switch.
            families = list(iface.configured_families())
            families += [f for f in (4, 6)
                         if (iface.name, f) in pending_dhcp and f not in families]
            for family in sorted(families):
                members = [
                    IpNode.from_address(
                        addr, iface.name,
                        alias=self._aliases.get(_alias_key(family, addr.cidr), ""),
                        pending_remove=(iface.name, addr.cidr) in removed_pending,
                    )
                    for addr in iface.addresses_for(family)
                    if not addr.dynamic  # DHCP/RA address shows in the group header
                ]
                group = IpGroup(iface, family, members,
                                pending_dhcp=(iface.name, family) in pending_dhcp,
                                host_dns=state.dns,
                                pending_dns_off=(iface.name, family) in dns_off_pending)
                ip_nodes.extend(members)
                ip_groups.append(group)
                groups_by_iface[iface.name].append(group)

        draft_nodes = [
            IpNode(
                d["family"], d["cidr"], None, draft_id=d["id"],
                alias=self._aliases.get(_alias_key(d["family"], d["cidr"]), ""),
                gateway=d.get("gateway", ""),
                dns=d.get("dns", []),
                dns_search=d.get("dns_search", []),
            )
            for d in self._drafts
        ]
        draft_vlan_nodes = [
            DraftVlanNode(dv["id"], dv["vlan_id"], dv["name"], dv["cidrs"])
            for dv in self._draft_vlans
        ]

        # Docker containers: one box per container, joined to the bridge of each
        # docker network it's on (the bridge already shows from the iproute2
        # probe; apply_docker tagged it with its network name). Published ports
        # are drawn as dashed PortEdges to the host uplink, after layout.
        bridge_for_net = {n.name: n.bridge for n in state.docker_networks}
        container_nodes = [ContainerNode(c) for c in state.containers]
        container_edges: list[tuple[str, str]] = []
        for node in container_nodes:
            for net in node.container.networks:
                bridge = bridge_for_net.get(net)
                if bridge in shown_names:
                    container_edges.append((if_nodes[bridge].key, node.key))

        # Topology graph for the auto-layout. These are exactly the same
        # relationships drawn as edges below (vlan->parent, member->master,
        # veth peer<->peer, interface->IP groups), built once so the layout
        # graph and the drawn cables can never drift apart. The layout engine
        # itself lives in core.layout (Qt-free) and stays unit-tested.
        node_by_key: dict[str, BaseNode] = {n.key: n for n in if_nodes.values()}
        for group in ip_groups:
            node_by_key[group.key] = group
        for node in container_nodes:
            node_by_key[node.key] = node

        graph_edges: list[tuple[str, str]] = []
        for iface in shown:
            key = if_nodes[iface.name].key
            if iface.kind == "vlan" and iface.vlan_parent in shown_names:
                graph_edges.append((if_nodes[iface.vlan_parent].key, key))
            if iface.master and iface.master in shown_names:
                graph_edges.append((key, if_nodes[iface.master].key))
            if iface.peer and iface.peer in shown_names and iface.name < iface.peer:
                graph_edges.append((key, if_nodes[iface.peer].key))
        for group in ip_groups:
            graph_edges.append((if_nodes[group.iface.name].key, group.key))
        graph_edges.extend(container_edges)

        # Layout-only edges (placement, not drawn): tie each container to the
        # host uplink so a container-bearing docker network flows rightward *from
        # the physical NIC* — uplink → container → bridge → bridge IP — instead of
        # its bridge floating in column 0. We hang containers off the uplink's
        # IPv4 *protocol box* (the box their RouteEdges actually land on) rather
        # than the bare NIC, so they sit one column right of it and the dashed/
        # dotted lines fan out cleanly instead of stacking collinear beneath it;
        # fall back to the NIC when that group isn't shown. A docker bridge with
        # NO containers — e.g. an unused docker0 — carries nothing from the host,
        # so it is left OUT here and floats as its own island.
        uplink = state.uplink()
        uplink_node = if_nodes.get(uplink.name) if uplink else None
        egress_group = self._group_of(ip_groups, uplink.name, 4) if uplink else None
        container_anchor = egress_group or uplink_node
        layout_edges = list(graph_edges)
        if container_anchor is not None:
            layout_edges += [(container_anchor.key, n.key) for n in container_nodes]

        # Physical NICs seed the left column; everything flows rightward from
        # them. The priority order (physical first, loopback last, then by name)
        # breaks layout ties and orders the vertically-stacked components. An
        # interface's IP groups follow it, so they sit nearest their link.
        sources = [if_nodes[i.name].key for i in shown if i.kind == "physical"]
        ranked = sorted(
            shown, key=lambda i: (i.kind == "loopback", i.kind != "physical", i.name)
        )
        priority: list[str] = []
        for iface in ranked:
            priority.append(if_nodes[iface.name].key)
            priority.extend(g.key for g in groups_by_iface[iface.name])
        priority.extend(n.key for n in container_nodes)

        for node in [*if_nodes.values(), *ip_nodes, *draft_nodes, *draft_vlan_nodes,
                     *container_nodes]:
            scene.addItem(node)
            node.drag_finished.connect(self._make_drop_handler(node))
            node.drag_finished.connect(self._save_state)
            # Drafts remember their own position (in their draft record) below.
            is_draft_box = (isinstance(node, IpNode) and node.is_draft) \
                or isinstance(node, DraftVlanNode)
            if node.key and not is_draft_box:
                node.moved.connect(self._make_position_saver(node))
        for group in ip_groups:
            scene.addItem(group)
            group.drag_finished.connect(self._save_state)

        # The host-wide resolvers, drawn as a frame around the whole diagram with
        # only its title bar interactive (it applies to everything). It's pinned
        # and re-fitted by wrap() below, so it has no saved position of its own.
        dns_node: SystemDns | None = None
        if state.dns or state.dns_search or self._manual_dns:
            dns_node = SystemDns(
                state.dns, state.dns_search, self._manual_dns, state.resolver_origin
            )
            scene.addItem(dns_node)

        # Draw those same relationships as straight, centre-to-centre cables
        # under the boxes (the veth `name < peer` guard already drew its shared
        # cable once when graph_edges was built).
        for a, b in graph_edges:
            scene.addItem(Edge(node_by_key[a], node_by_key[b]))

        # Leave room at the top for the DNS frame's title bar; the diagram lays
        # out below it and the frame wraps the whole thing once it's positioned.
        start_y = MARGIN
        if dns_node is not None:
            start_y = MARGIN + dns_node.top_reserve()

        boxes = [
            layout.Box(n.key, n.boundingRect().width(), n.boundingRect().height())
            for n in if_nodes.values()
        ]
        boxes += [
            layout.Box(g.key, g.block_width(), g.block_height()) for g in ip_groups
        ]
        boxes += [
            layout.Box(n.key, n.boundingRect().width(), n.boundingRect().height())
            for n in container_nodes
        ]
        placement = layout.solve(
            boxes, layout_edges, sources, priority,
            margin_x=MARGIN, margin_y=start_y, col_gap=COL_GAP, row_gap=V_GAP,
        )
        # The saver is muted for the whole placement pass (see _laying_out), so
        # these setPos() calls don't masquerade as user-chosen positions.
        self._laying_out = True
        for key, (x, y) in placement.items():
            node = node_by_key[key]
            if isinstance(node, RegionNode):
                node.arrange(x, y)  # places its member boxes + frame
            else:
                node.setPos(x, y)

        for draft, node in zip(self._drafts, draft_nodes, strict=True):
            node.setPos(draft["pos"])
            node.moved.connect(self._make_draft_position_saver(draft, node))
        for dv, node in zip(self._draft_vlans, draft_vlan_nodes, strict=True):
            node.setPos(dv["pos"])
            node.moved.connect(self._make_draft_position_saver(dv, node))

        # Remembered positions win over the automatic layout; members moving
        # makes their group reflow around them. _positions is intact here
        # because the saver was muted through the auto pass above.
        remembered = [*if_nodes.values(), *ip_nodes, *container_nodes]
        for node in remembered:
            if node.key in self._positions:
                node.setPos(self._positions[node.key])
        self._laying_out = False

        # Member moves no longer auto-grow their frame (so an address can be
        # dragged out), so wrap each group once here, after every member has its
        # final position.
        for group in ip_groups:
            group.refresh()

        # A container's L3 lines, drawn after layout so they sit on top. Both land
        # on a protocol (IP-config) box, never the bare NIC — forwarding and the
        # default route are address-level, and a publish can be pinned to one
        # host IP. The uplink coupling for placement is already in the layout.
        #
        #  - forward (dashed, labelled): each published port resolves to the box
        #    holding the host address it binds to — the specific address's group
        #    when pinned, else the uplink's group for its family (0.0.0.0 → v4,
        #    :: → v6). Ports sharing a box collapse into one labelled line.
        #  - egress (dotted, no label): the always-on outbound path via the host's
        #    v4 default route. Suppressed when a forward already links this
        #    container to that same box, so the two never sit collinear.
        for node in container_nodes:
            forwards: dict[str, tuple[RegionNode, list]] = {}
            if self._show_forwards:
                for port in node.container.ports:
                    grp = self._forward_anchor(
                        port, ip_groups, uplink, node.container,
                        bridge_for_net, shown_names,
                    )
                    if grp is None or grp is node:
                        continue
                    forwards.setdefault(grp.key, (grp, []))[1].append(port)
                for grp, ports in forwards.values():
                    label = "\n".join(p.label() for p in ports)  # one forward per line
                    scene.addItem(RouteEdge(node, grp, label, kind="forward"))
            if self._show_egress:
                egress = self._egress_anchor(
                    ip_groups, uplink, node.container, bridge_for_net, shown_names
                )
                if egress is not None and egress is not node \
                        and egress.key not in forwards:
                    scene.addItem(RouteEdge(node, egress, kind="egress"))

        # The DNS frame wraps the finished diagram, so fit it last — after every
        # node (including remembered positions) has settled.
        if dns_node is not None:
            content = QRectF()
            for node in [*if_nodes.values(), *ip_nodes, *ip_groups,
                         *draft_nodes, *draft_vlan_nodes, *container_nodes]:
                content = content.united(node.sceneBoundingRect())
            dns_node.wrap(content)

        rect = scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        scene.setSceneRect(rect)

    def auto_layout(self) -> None:
        self._positions.clear()
        self._save_state()
        self.populate(self._state)

    @staticmethod
    def _group_of(ip_groups, iface_name, family):
        """The IpGroup (protocol box) for one interface + family, or None."""
        for group in ip_groups:
            if group.iface.name == iface_name and group.family == family:
                return group
        return None

    @classmethod
    def _first_bridge_group(cls, ip_groups, container, bridge_for_net,
                            shown_names, family):
        """The protocol box of the first shown bridge ``container`` is on — the
        fallback anchor when there's no host uplink (no default route)."""
        for net in container.networks:
            bridge = bridge_for_net.get(net)
            if bridge in shown_names:
                group = cls._group_of(ip_groups, bridge, family)
                if group is not None:
                    return group
        return None

    @classmethod
    def _forward_anchor(cls, port, ip_groups, uplink, container,
                        bridge_for_net, shown_names):
        """The protocol box a published port lands on: the group holding the
        host address it binds to when pinned, else the uplink's group for the
        publish's family (0.0.0.0 → v4, :: → v6)."""
        if not port.all_host_ips:
            family = ip_family(port.host_ip) or 4
            for group in ip_groups:
                if any(a.address == port.host_ip
                       for a in group.iface.addresses_for(family)):
                    return group
        else:
            family = 6 if port.host_ip == "::" else 4
        if uplink is not None:
            group = cls._group_of(ip_groups, uplink.name, family)
            if group is not None:
                return group
        return cls._first_bridge_group(
            ip_groups, container, bridge_for_net, shown_names, family
        )

    @classmethod
    def _egress_anchor(cls, ip_groups, uplink, container, bridge_for_net,
                       shown_names):
        """The protocol box a container's outbound default route exits through:
        the uplink's IPv4 group (the default-route family), falling back to a
        bridge the container is on when there's no host uplink."""
        if uplink is not None:
            group = cls._group_of(ip_groups, uplink.name, 4)
            if group is not None:
                return group
        return cls._first_bridge_group(
            ip_groups, container, bridge_for_net, shown_names, 4
        )

    # ------------------------------------------------------------------ #
    # persisted state (drafts, positions, box names) — see core/store.py
    # ------------------------------------------------------------------ #
    def _load_state_for(self, label: str) -> None:
        self._host_label = label
        data = store.load_host(label)
        self._positions = {}
        for key, xy in data["positions"].items():
            if isinstance(xy, (list, tuple)) and len(xy) == 2:
                self._positions[key] = QPointF(float(xy[0]), float(xy[1]))
        self._aliases = {str(k): str(v) for k, v in data["aliases"].items()}
        self._manual_dns = [str(s) for s in data.get("manual_dns", [])]
        self._drafts = []
        for d in data["drafts"]:
            try:
                pos = d["pos"]
                self._drafts.append({
                    "id": new_draft_id(),
                    "family": int(d["family"]),
                    "cidr": str(d["cidr"]),
                    "gateway": str(d.get("gateway", "")),
                    "dns": [str(s) for s in d.get("dns", [])],
                    "dns_search": [str(s) for s in d.get("dns_search", [])],
                    "pos": QPointF(float(pos[0]), float(pos[1])),
                })
            except (KeyError, TypeError, ValueError, IndexError):
                continue  # skip any malformed draft, keep the rest
        self._draft_vlans = []
        for dv in data.get("draft_vlans", []):
            try:
                pos = dv["pos"]
                self._draft_vlans.append({
                    "id": new_draft_id(),
                    "vlan_id": int(dv["vlan_id"]),
                    "name": str(dv.get("name", "")),
                    "cidrs": [str(c) for c in dv.get("cidrs", [])],
                    "pos": QPointF(float(pos[0]), float(pos[1])),
                })
            except (KeyError, TypeError, ValueError, IndexError):
                continue

    def _save_state(self) -> None:
        if self._host_label is None:
            return
        store.save_host(self._host_label, {
            "positions": {k: [p.x(), p.y()] for k, p in self._positions.items()},
            "drafts": [
                {"family": d["family"], "cidr": d["cidr"],
                 "gateway": d.get("gateway", ""),
                 "dns": list(d.get("dns", [])),
                 "dns_search": list(d.get("dns_search", [])),
                 "pos": [d["pos"].x(), d["pos"].y()]}
                for d in self._drafts
            ],
            "draft_vlans": [
                {"vlan_id": dv["vlan_id"], "name": dv["name"], "cidrs": list(dv["cidrs"]),
                 "pos": [dv["pos"].x(), dv["pos"].y()]}
                for dv in self._draft_vlans
            ],
            "aliases": dict(self._aliases),
            "manual_dns": list(self._manual_dns),
        })

    def set_manual_dns(self, servers: list[str]) -> None:
        """Replace the user's host-wide manual resolvers and redraw."""
        self._manual_dns = list(servers)
        self._save_state()
        self.populate(self._state)

    def set_ip_name(self, family: int, cidr: str, name: str) -> None:
        """Give (or, with an empty name, clear) a box's free-form label.

        Keyed by address, so the name follows it across moves and detaches.
        """
        key = _alias_key(family, cidr)
        if name:
            self._aliases[key] = name
        else:
            self._aliases.pop(key, None)
        self._save_state()
        self.populate(self._state)

    # ------------------------------------------------------------------ #
    # drafts (IP configs not attached to any interface yet)
    # ------------------------------------------------------------------ #
    def add_draft(self, family: int, cidr: str, scene_pos: QPointF, name: str = "",
                  gateway: str = "", dns: list[str] | None = None,
                  dns_search: list[str] | None = None) -> None:
        self._drafts.append({
            "id": new_draft_id(), "family": family, "cidr": cidr,
            "gateway": gateway, "dns": list(dns or []),
            "dns_search": list(dns_search or []), "pos": scene_pos,
        })
        if name:
            self._aliases[_alias_key(family, cidr)] = name
        self._save_state()
        self.populate(self._state)

    def update_draft(self, draft_id: int, cidr: str, gateway: str = "",
                     dns: list[str] | None = None,
                     dns_search: list[str] | None = None) -> None:
        for d in self._drafts:
            if d["id"] == draft_id:
                old_key = _alias_key(d["family"], d["cidr"])
                new_key = _alias_key(d["family"], cidr)
                if old_key != new_key and old_key in self._aliases:
                    self._aliases[new_key] = self._aliases.pop(old_key)
                d["cidr"] = cidr
                d["gateway"] = gateway
                d["dns"] = list(dns or [])
                d["dns_search"] = list(dns_search or [])
        self._save_state()
        self.populate(self._state)

    def remove_draft(self, draft_id: int) -> None:
        self._drafts = [d for d in self._drafts if d["id"] != draft_id]
        self._save_state()
        self.populate(self._state)

    # ------------------------------------------------------------------ #
    # draft VLANs (a VLAN configured here, created on a parent later)
    # ------------------------------------------------------------------ #
    def add_draft_vlan(self, vlan_id: int, name: str, scene_pos: QPointF) -> None:
        self._draft_vlans.append({
            "id": new_draft_id(), "vlan_id": vlan_id, "name": name,
            "cidrs": [], "pos": scene_pos,
        })
        self._save_state()
        self.populate(self._state)

    def update_draft_vlan(self, draft_id: int, vlan_id: int, name: str) -> None:
        for dv in self._draft_vlans:
            if dv["id"] == draft_id:
                dv["vlan_id"] = vlan_id
                dv["name"] = name
        self._save_state()
        self.populate(self._state)

    def add_draft_vlan_address(self, draft_id: int, cidr: str) -> None:
        for dv in self._draft_vlans:
            if dv["id"] == draft_id and cidr not in dv["cidrs"]:
                dv["cidrs"].append(cidr)
        self._save_state()
        self.populate(self._state)

    def remove_draft_vlan_address(self, draft_id: int, cidr: str) -> None:
        for dv in self._draft_vlans:
            if dv["id"] == draft_id:
                dv["cidrs"] = [c for c in dv["cidrs"] if c != cidr]
        self._save_state()
        self.populate(self._state)

    def remove_draft_vlan(self, draft_id: int) -> None:
        self._draft_vlans = [dv for dv in self._draft_vlans if dv["id"] != draft_id]
        self._save_state()
        self.populate(self._state)

    def move_draft_to_vlan(self, ip_draft_id: int, cidr: str, vlan_draft_id: int) -> None:
        """Fold a free IP draft into a draft VLAN's pending addresses."""
        for dv in self._draft_vlans:
            if dv["id"] == vlan_draft_id and cidr not in dv["cidrs"]:
                dv["cidrs"].append(cidr)
        self._drafts = [d for d in self._drafts if d["id"] != ip_draft_id]
        self._save_state()
        self.populate(self._state)

    # ------------------------------------------------------------------ #
    # drop detection
    # ------------------------------------------------------------------ #
    def _make_drop_handler(self, node: BaseNode):
        return lambda: self._node_dropped(node)

    def _make_position_saver(self, node: BaseNode):
        # Record a position only when the *user* moves a box, not when the
        # auto-layout does (see self._laying_out) — otherwise every re-probe's
        # tidy pass would freeze auto guesses in as if the user had chosen them.
        def save() -> None:
            if not self._laying_out:
                self._positions[node.key] = node.pos()
        return save

    def _make_draft_position_saver(self, draft: dict, node: BaseNode):
        return lambda: draft.__setitem__("pos", node.pos())

    def _node_dropped(self, node: BaseNode) -> None:
        if isinstance(node, IpNode):
            self._ip_node_dropped(node)
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
        elif isinstance(node, DraftVlanNode):
            target = self._drop_target(node, (NicNode, GroupNode))
            if target is not None and self._can_parent_vlan(target):
                self.draft_vlan_dropped.emit(node, target)

    def _ip_node_dropped(self, node: IpNode) -> None:
        """Resolve where an address box was dropped.

        Dropping it on a family group's title bar (or on a link box) attaches
        the address to that interface; dropping it clear of its own frame
        detaches it to a draft; dropping it back in its own group leaves it.
        """
        ctrl = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        # A free IP draft can be folded into a draft VLAN's pending addresses.
        if node.is_draft:
            vlan_draft = self._drop_target(node, (DraftVlanNode,))
            if vlan_draft is not None:
                self.move_draft_to_vlan(node.draft_id, node.cidr, vlan_draft.draft_id)
                return
        target = self._region_header_target(node) or self._drop_target(
            node, (NicNode, GroupNode, VlanNode)
        )
        if target is not None:
            if target.iface.name != node.parent_name:
                self.ip_dropped.emit(node, target, ctrl)
            return  # dropped on its own group/link: leave it in place
        if not node.is_draft and self._left_own_region(node):
            self.ip_detached.emit(node)

    def _region_header_target(self, node: BaseNode):
        """The Ip group whose title bar the dragged box overlaps most, if any."""
        own = node.sceneBoundingRect()
        best, best_area = None, 0.0
        for item in self.scene().items():
            if not isinstance(item, IpGroup):
                continue
            header = item.header_rect_scene()
            if header.isNull():
                continue
            overlap = header.intersected(own)
            area = overlap.width() * overlap.height()
            if area > best_area:
                best, best_area = item, area
        return best if best_area > 0 else None

    def _can_parent_vlan(self, target) -> bool:
        """A VLAN can hang off a physical NIC or a bond/bridge/team, not a
        loopback (or, for now, another VLAN)."""
        iface = getattr(target, "iface", None)
        return iface is not None and iface.kind in ("physical", "bond", "bridge", "team")

    def _left_own_region(self, node: IpNode) -> bool:
        """True if ``node`` no longer overlaps the frame of the group it belongs
        to — i.e. it was dragged out and should detach to a draft."""
        for item in self.scene().items():
            if (
                isinstance(item, IpGroup)
                and item.iface.name == node.parent_name
                and item.family == node.family
            ):
                return not item.frame_rect().intersects(node.sceneBoundingRect())
        return True

    def _drop_target(self, node: BaseNode, kinds: tuple):
        # Overlap is measured against full bounding rects rather than colliding
        # shapes, so an Ip group (whose interactive shape is only its header)
        # still counts as a target across its whole framed area.
        own_rect = node.sceneBoundingRect()
        own_area = own_rect.width() * own_rect.height()
        best, best_area = None, 0.0
        for item in self.scene().items():
            if item is node or not isinstance(item, kinds):
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
        # A solid-bodied region (an IP group) owns its whole frame, so a
        # right-click anywhere in it opens the group menu — same as on its title
        # bar. A see-through region (System DNS) owns only its header strip, so a
        # right-click in its body falls through to the box under it or the canvas.
        # The DNS frame's title bar carries the node menu (manual resolvers), not
        # the per-family IP-group menu.
        if isinstance(item, SystemDns):
            self.node_menu_requested.emit(item, event.globalPos())
            event.accept()
            return
        if isinstance(item, RegionNode):
            self.region_menu_requested.emit(item, event.globalPos())
            event.accept()
            return
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
