"""Main window: host picker, canvas, and handlers that turn canvas gestures
into confirmed command plans.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
)

import netgrip
from netgrip.core import actions
from netgrip.core.demo import DEMO_DNS, DEMO_DNS_SEARCH, demo_interfaces
from netgrip.core.model import GROUP_KINDS, HostState, Interface
from netgrip.core.probe import probe, probe_dns
from netgrip.core.runner import DemoRunner, LocalRunner, Runner, SSHRunner
from netgrip.core.sshhosts import ssh_config_hosts
from netgrip.ui.canvas import Canvas
from netgrip.ui.dialogs import (
    BondDialog,
    IpConfigDialog,
    LinkPropertiesDialog,
    VlanDialog,
    confirm_commands,
)
from netgrip.ui.items import GroupNode, IpNode, NicNode, VlanNode
from netgrip.ui.worker import run_in_background

_LOCAL = "__local__"
_DEMO = "__demo__"
_CUSTOM = "__custom__"


class MainWindow(QMainWindow):
    def __init__(self, initial_host: str | None = None, demo: bool = False):
        super().__init__()
        self.setWindowTitle("NetGrip")
        self.setWindowIcon(QIcon.fromTheme("network-wired"))
        self.resize(1100, 720)

        self.runner: Runner = LocalRunner()
        self.state: HostState | None = None
        self._busy = False

        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.ip_dropped.connect(self._on_ip_dropped)
        self.canvas.nic_dropped.connect(self._on_nic_dropped)
        self.canvas.vlan_dropped.connect(self._on_vlan_dropped)
        self.canvas.node_menu_requested.connect(self._show_node_menu)
        self.canvas.canvas_menu_requested.connect(self._show_canvas_menu)

        self._build_toolbar()
        self.statusBar().showMessage("Ready")

        if demo:
            self._select_host_data(_DEMO)
        elif initial_host:
            self.host_combo.setCurrentText(initial_host)
            self._connect_to(SSHRunner(initial_host))
        else:
            self.refresh()

    # ------------------------------------------------------------------ #
    # chrome
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        bar.addWidget(QLabel(" Host: "))
        self.host_combo = QComboBox()
        self.host_combo.setMinimumWidth(220)
        self.host_combo.addItem("Local (this machine)", _LOCAL)
        ssh_hosts = ssh_config_hosts()
        if ssh_hosts:
            self.host_combo.insertSeparator(self.host_combo.count())
            for host in ssh_hosts:
                self.host_combo.addItem(f"ssh: {host}", host)
        self.host_combo.insertSeparator(self.host_combo.count())
        self.host_combo.addItem("Other host (ssh)…", _CUSTOM)
        self.host_combo.addItem("Demo (no changes applied)", _DEMO)
        self.host_combo.activated.connect(self._host_picked)
        bar.addWidget(self.host_combo)

        refresh = QAction("Refresh", self)
        refresh.setShortcut(QKeySequence(Qt.Key.Key_F5))
        refresh.triggered.connect(self.refresh)
        bar.addAction(refresh)

        fit = QAction("Fit view", self)
        fit.triggered.connect(self.canvas.fit_all)
        bar.addAction(fit)

        relayout = QAction("Auto-layout", self)
        relayout.triggered.connect(self.canvas.auto_layout)
        bar.addAction(relayout)

        self.loopback_action = QAction("Show loopback", self)
        self.loopback_action.setCheckable(True)
        self.loopback_action.toggled.connect(
            lambda checked: self.canvas.populate(self.state, checked)
        )
        bar.addAction(self.loopback_action)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About NetGrip", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About NetGrip",
            f"<b>NetGrip {netgrip.__version__}</b><br>"
            "Visual, drag-and-drop network interface manager.<br>"
            "Changes are applied with iproute2 and affect the running "
            "system only.<br><br>"
            '<a href="https://github.com/theyoungrossco/netgrip">'
            "github.com/theyoungrossco/netgrip</a>",
        )

    # ------------------------------------------------------------------ #
    # host switching & probing
    # ------------------------------------------------------------------ #
    def _host_picked(self, index: int) -> None:
        self._select_host_data(self.host_combo.itemData(index))

    def _select_host_data(self, data: str) -> None:
        if data == _CUSTOM:
            host, ok = QInputDialog.getText(
                self, "Connect over SSH", "Host (anything `ssh` accepts, e.g. user@10.0.0.2):"
            )
            if not ok or not host.strip():
                return
            self._connect_to(SSHRunner(host.strip()))
        elif data == _DEMO:
            idx = self.host_combo.findData(_DEMO)
            if idx >= 0:
                self.host_combo.setCurrentIndex(idx)
            self._connect_to(DemoRunner())
        elif data == _LOCAL:
            self._connect_to(LocalRunner())
        elif data:
            self._connect_to(SSHRunner(data))

    def _connect_to(self, runner: Runner) -> None:
        self.runner.close()
        self.runner = runner
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        runner = self.runner
        if isinstance(runner, DemoRunner):
            self._set_state(demo_interfaces(), DEMO_DNS, DEMO_DNS_SEARCH, can_edit_dns=False)
            return
        self._set_busy(True, f"Reading interfaces on {runner.label}…")

        def work() -> tuple:
            interfaces = probe(runner)
            servers, search, can_edit = probe_dns(runner)
            return interfaces, servers, search, can_edit

        run_in_background(
            work,
            on_done=lambda res: (self._set_busy(False), self._set_state(*res)),
            on_error=lambda msg: (self._set_busy(False), self._show_error(msg)),
        )

    def _set_state(self, interfaces: list[Interface], dns: list[str] | None = None,
                   dns_search: list[str] | None = None, can_edit_dns: bool = False) -> None:
        self.state = HostState(
            self.runner.label, interfaces,
            list(dns or []), list(dns_search or []), can_edit_dns,
        )
        self.canvas.populate(self.state, self.loopback_action.isChecked())
        dns_note = f" · DNS {', '.join(self.state.dns)}" if self.state.dns else ""
        self.statusBar().showMessage(
            f"{self.runner.label}: {len(interfaces)} interfaces{dns_note}"
        )

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self._busy = busy
        if message:
            self.statusBar().showMessage(message)

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage("Error")
        QMessageBox.critical(self, "NetGrip", message)

    # ------------------------------------------------------------------ #
    # applying plans
    # ------------------------------------------------------------------ #
    def _apply(self, title: str, plan: list[list[str]], on_success=None) -> None:
        if self._busy or not plan:
            return
        if not confirm_commands(self, title, plan, self.runner.label):
            return
        runner = self.runner
        self._set_busy(True, f"{title}…")

        def done(_result) -> None:
            self._set_busy(False)
            self.statusBar().showMessage(f"{title}: done")
            if on_success:
                on_success()
            self.refresh()

        run_in_background(
            lambda: runner.run_privileged(plan),
            on_done=done,
            on_error=lambda msg: (self._set_busy(False), self._show_error(msg)),
        )

    # ------------------------------------------------------------------ #
    # drag-and-drop gestures
    # ------------------------------------------------------------------ #
    def _on_ip_dropped(self, node: IpNode, target, clone: bool) -> None:
        target_name = target.iface.name
        if node.is_draft:
            draft_id = node.draft_id
            self._apply(
                f"Attach IPv{node.family} config to {target_name}",
                actions.plan_add_addresses(target_name, [node.cidr]),
                on_success=lambda: self.canvas.remove_draft(draft_id),
            )
        elif clone:
            self._apply(
                f"Clone IPv{node.family} config to {target_name}",
                actions.plan_add_addresses(target_name, [node.cidr]),
            )
        else:
            self._apply(
                f"Move IPv{node.family} config from {node.parent_name} to {target_name}",
                actions.plan_move_addresses(node.parent_name, target_name, [node.cidr]),
            )

    def _on_nic_dropped(self, node: NicNode, target) -> None:
        nic = node.iface.name
        if isinstance(target, GroupNode):
            self._apply(
                f"Add {nic} to {target.iface.name}",
                actions.plan_add_member(target.iface.name, nic),
            )
        else:
            self._new_bond_dialog(preselected=[nic, target.iface.name])

    def _on_vlan_dropped(self, node: VlanNode, target) -> None:
        new_parent = target.iface.name
        self._apply(
            f"Move {node.iface.name} to {new_parent}",
            actions.plan_move_vlan(node.iface, new_parent),
        )

    def _new_bond_dialog(self, preselected: list[str]) -> None:
        if not self.state:
            return
        free = [i.name for i in self.state.free_nics()]
        # Preselected NICs may include ones already enslaved elsewhere; the
        # dialog only offers genuinely free NICs.
        dialog = BondDialog(self, free, preselected, self.state.link_names())
        if dialog.exec():
            self._apply(
                f"Create bond {dialog.name}",
                actions.plan_create_bond(dialog.name, dialog.mode, dialog.members),
            )

    # ------------------------------------------------------------------ #
    # context menus
    # ------------------------------------------------------------------ #
    def _show_node_menu(self, node, global_pos: QPoint) -> None:
        if not self.state:
            return
        menu = QMenu(self)
        if isinstance(node, IpNode):
            self._fill_ip_menu(menu, node)
        elif isinstance(node, GroupNode):
            self._fill_group_menu(menu, node.iface)
        elif isinstance(node, VlanNode):
            self._fill_vlan_menu(menu, node.iface)
        elif isinstance(node, NicNode):
            self._fill_nic_menu(menu, node.iface)
        if not menu.isEmpty():
            menu.exec(global_pos)

    def _add_common_iface_items(self, menu: QMenu, iface: Interface) -> None:
        menu.addAction(
            "Add IPv4 config…", partial(self._add_ip_dialog, iface.name, 4)
        )
        menu.addAction(
            "Add IPv6 config…", partial(self._add_ip_dialog, iface.name, 6)
        )
        if iface.kind != "vlan":
            menu.addAction("Add VLAN…", partial(self._add_vlan_dialog, iface.name))
        if iface.kind != "loopback":
            menu.addAction("Properties…", partial(self._link_properties_dialog, iface))
        menu.addSeparator()
        if iface.is_up:
            menu.addAction(
                f"Take {iface.name} down",
                partial(self._apply, f"Take {iface.name} down",
                        actions.plan_set_link(iface.name, False)),
            )
        else:
            menu.addAction(
                f"Bring {iface.name} up",
                partial(self._apply, f"Bring {iface.name} up",
                        actions.plan_set_link(iface.name, True)),
            )

    def _fill_nic_menu(self, menu: QMenu, iface: Interface) -> None:
        self._add_common_iface_items(menu, iface)
        menu.addSeparator()
        if iface.master:
            menu.addAction(
                f"Remove from {iface.master}",
                partial(self._apply, f"Remove {iface.name} from {iface.master}",
                        actions.plan_remove_member(iface.name)),
            )
        elif iface.kind == "physical":
            menu.addAction(
                "Create bond with this NIC…",
                partial(self._new_bond_dialog, [iface.name]),
            )

    def _fill_group_menu(self, menu: QMenu, iface: Interface) -> None:
        self._add_common_iface_items(menu, iface)
        menu.addSeparator()
        if iface.kind == "bond":
            mode_menu = menu.addMenu("Bond mode")
            for value, label in actions.BOND_MODES.items():
                action = mode_menu.addAction(
                    label,
                    partial(self._apply, f"Set {iface.name} mode to {value}",
                            actions.plan_set_bond_mode(iface.name, value)),
                )
                action.setCheckable(True)
                action.setChecked(value == iface.bond_mode)
        add_menu = menu.addMenu("Add member")
        free = self.state.free_nics()
        add_menu.setEnabled(bool(free))
        for nic in free:
            add_menu.addAction(
                nic.name,
                partial(self._apply, f"Add {nic.name} to {iface.name}",
                        actions.plan_add_member(iface.name, nic.name)),
            )
        members = self.state.members_of(iface.name)
        if members:
            remove_menu = menu.addMenu("Remove member")
            for member in members:
                remove_menu.addAction(
                    member.name,
                    partial(self._apply, f"Remove {member.name} from {iface.name}",
                            actions.plan_remove_member(member.name)),
                )
        menu.addSeparator()
        menu.addAction(
            f"Delete {iface.name}",
            partial(self._apply, f"Delete {iface.name} (members are released)",
                    actions.plan_delete_link(iface.name)),
        )

    def _fill_vlan_menu(self, menu: QMenu, iface: Interface) -> None:
        self._add_common_iface_items(menu, iface)
        menu.addSeparator()
        menu.addAction(
            f"Delete {iface.name}",
            partial(self._apply, f"Delete VLAN {iface.name}",
                    actions.plan_delete_link(iface.name)),
        )

    def _fill_ip_menu(self, menu: QMenu, node: IpNode) -> None:
        if node.is_draft:
            attach = menu.addMenu("Attach to")
            for iface in self._attachable_ifaces():
                attach.addAction(
                    iface.name,
                    partial(self._attach_draft, node, iface.name),
                )
            menu.addAction("Edit address…", partial(self._edit_ip_dialog, node))
            menu.addAction("Set name…", partial(self._name_ip_dialog, node))
            menu.addSeparator()
            menu.addAction(
                "Delete draft", partial(self.canvas.remove_draft, node.draft_id)
            )
            return

        menu.addAction("Edit address…", partial(self._edit_ip_dialog, node))
        menu.addAction("Set name…", partial(self._name_ip_dialog, node))
        menu.addAction("Clone (as draft)", partial(self._clone_ip, node))
        move = menu.addMenu("Move to")
        for iface in self._attachable_ifaces(exclude=node.parent_name):
            move.addAction(
                iface.name,
                partial(self._apply,
                        f"Move IPv{node.family} config to {iface.name}",
                        actions.plan_move_addresses(node.parent_name, iface.name, [node.cidr])),
            )
        menu.addSeparator()
        menu.addAction(
            f"Detach from {node.parent_name} (keep as draft)",
            partial(self._detach_ip, node),
        )
        menu.addAction(
            "Delete address",
            partial(self._apply,
                    f"Delete IPv{node.family} config from {node.parent_name}",
                    actions.plan_remove_addresses(node.parent_name, [node.cidr])),
        )

    def _attachable_ifaces(self, exclude: str | None = None) -> list[Interface]:
        if not self.state:
            return []
        return [
            i for i in self.state.interfaces
            if i.name != exclude
            and i.master is None
            and (i.kind in ("physical", "vlan", "loopback") or i.kind in GROUP_KINDS)
        ]

    def _show_canvas_menu(self, global_pos: QPoint, scene_pos: QPointF) -> None:
        menu = QMenu(self)
        menu.addAction(
            "New IPv4 config (draft)…", partial(self._new_draft_dialog, 4, scene_pos)
        )
        menu.addAction(
            "New IPv6 config (draft)…", partial(self._new_draft_dialog, 6, scene_pos)
        )
        menu.addSeparator()
        menu.addAction("Refresh", self.refresh)
        menu.addAction("Auto-layout", self.canvas.auto_layout)
        menu.exec(global_pos)

    # ------------------------------------------------------------------ #
    # dialogs / draft helpers
    # ------------------------------------------------------------------ #
    def _add_ip_dialog(self, ifname: str, family: int) -> None:
        dialog = IpConfigDialog(self, family, title=f"Add IPv{family} config to {ifname}")
        if dialog.exec():
            cidr, name = dialog.cidr, dialog.name
            self._apply(
                f"Add IPv{family} config to {ifname}",
                actions.plan_add_addresses(ifname, [cidr]),
                on_success=(lambda: self.canvas.set_ip_name(family, cidr, name)) if name else None,
            )

    def _edit_ip_dialog(self, node: IpNode) -> None:
        dialog = IpConfigDialog(self, node.family, initial=node.cidr, name=node.alias)
        if not dialog.exec():
            return
        if node.is_draft:
            self.canvas.update_draft(node.draft_id, dialog.cidr)
            self.canvas.set_ip_name(node.family, dialog.cidr, dialog.name)
            return
        cidr, name, family, parent = dialog.cidr, dialog.name, node.family, node.parent_name
        rename = lambda: self.canvas.set_ip_name(family, cidr, name)  # noqa: E731
        if cidr != node.cidr:
            plan = actions.plan_remove_addresses(parent, [node.cidr]) + \
                actions.plan_add_addresses(parent, [cidr])
            self._apply(f"Edit IPv{family} config on {parent}", plan, on_success=rename)
        else:
            rename()  # only the name changed; no kernel change needed

    def _add_vlan_dialog(self, ifname: str) -> None:
        dialog = VlanDialog(self, ifname, self.state.link_names() if self.state else set())
        if dialog.exec():
            self._apply(
                f"Create VLAN {dialog.vlan_id} on {ifname}",
                actions.plan_create_vlan(ifname, dialog.vlan_id, dialog.name),
            )

    def _new_draft_dialog(self, family: int, scene_pos: QPointF) -> None:
        dialog = IpConfigDialog(self, family, title=f"New IPv{family} config (draft)")
        if dialog.exec():
            self.canvas.add_draft(family, dialog.cidr, scene_pos, name=dialog.name)

    def _name_ip_dialog(self, node: IpNode) -> None:
        text, ok = QInputDialog.getText(
            self, "Name this address", "Name (blank to clear):", text=node.alias
        )
        if ok:
            self.canvas.set_ip_name(node.family, node.cidr, text.strip())

    def _attach_draft(self, node: IpNode, ifname: str) -> None:
        draft_id = node.draft_id
        self._apply(
            f"Attach IPv{node.family} config to {ifname}",
            actions.plan_add_addresses(ifname, [node.cidr]),
            on_success=lambda: self.canvas.remove_draft(draft_id),
        )

    def _clone_ip(self, node: IpNode) -> None:
        self.canvas.add_draft(node.family, node.cidr, node.pos() + QPointF(30, 30))

    def _detach_ip(self, node: IpNode) -> None:
        family, cidr, pos = node.family, node.cidr, node.pos()
        self._apply(
            f"Detach IPv{family} config from {node.parent_name}",
            actions.plan_remove_addresses(node.parent_name, [cidr]),
            on_success=lambda: self.canvas.add_draft(family, cidr, pos),
        )

    def _link_properties_dialog(self, iface: Interface) -> None:
        if not self.state:
            return
        others = self.state.link_names() - {iface.name}
        dlg = LinkPropertiesDialog(
            self, iface, others,
            dns=self.state.dns, dns_search=self.state.dns_search,
            can_edit_dns=self.state.can_edit_dns,
        )
        if not dlg.exec():
            return
        plan: list[list[str]] = []
        changed: list[str] = []
        # Link-level changes apply under the current name; rename goes last.
        if dlg.mtu != iface.mtu:
            plan += actions.plan_set_mtu(iface.name, dlg.mtu)
            changed.append("MTU")
        if dlg.mac != iface.mac:
            plan += actions.plan_set_mac(iface.name, dlg.mac)
            changed.append("MAC")
        if dlg.link_alias != iface.alias:
            plan += actions.plan_set_alias(iface.name, dlg.link_alias)
            changed.append("alias")
        # Gateway/DNS only when Static is chosen; Dynamic leaves DHCP alone.
        if dlg.gateway_static:
            if dlg.gateway and dlg.gateway != iface.gateway:
                plan += actions.plan_set_gateway(iface.name, dlg.gateway)
                changed.append("gateway")
            elif not dlg.gateway and iface.gateway:
                plan += actions.plan_clear_gateway(iface.name)
                changed.append("gateway")
        if dlg.dns_static and dlg.dns_servers != self.state.dns:
            plan += actions.plan_set_dns(iface.name, dlg.dns_servers, dlg.dns_search)
            changed.append("DNS")
        if dlg.new_name != iface.name:
            plan += actions.plan_rename_link(iface.name, dlg.new_name, iface.is_up)
            changed.append("name")
        if plan:
            self._apply(f"Update {iface.name} ({', '.join(changed)})", plan)
