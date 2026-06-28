"""Main window: host picker, canvas, and handlers that turn canvas gestures
into confirmed command plans.
"""

from __future__ import annotations

import ipaddress
import secrets
from functools import partial

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QWidget,
)

import netgrip
from netgrip.core import actions, persist, persist_link
from netgrip.core.backends import Backend, detect_backend
from netgrip.core.demo import (
    DEMO_BACKEND,
    DEMO_DNS,
    DEMO_DNS_SEARCH,
    demo_docker,
    demo_interfaces,
)
from netgrip.core.model import (
    GROUP_KINDS,
    Address,
    Container,
    DockerNetwork,
    Gateway,
    HostState,
    Interface,
    ip_family,
)
from netgrip.core.probe import apply_docker, apply_link_dns, probe, probe_dns, probe_docker
from netgrip.core.runner import (
    IS_WINDOWS,
    DemoRunner,
    LocalRunner,
    Runner,
    SSHRunner,
    UnconnectedRunner,
    hostkey_failure,
    is_auth_failure,
    sudo_auth_failed,
)
from netgrip.core.sshhosts import ssh_config_hosts
from netgrip.ui import theme
from netgrip.ui.branding import app_icon
from netgrip.ui.canvas import Canvas
from netgrip.ui.dialogs import (
    CONFIRM_CANCEL,
    CONFIRM_TRY,
    BondDialog,
    DraftVlanDialog,
    IpConfigDialog,
    IpGroupDialog,
    LinkPropertiesDialog,
    ManualDnsDialog,
    TryCountdownDialog,
    VlanDialog,
    confirm_commands,
)
from netgrip.ui.items import (
    DraftVlanNode,
    GroupNode,
    IpGroup,
    IpNode,
    NicNode,
    SystemDns,
    VlanNode,
)
from netgrip.ui.legend import Legend
from netgrip.ui.worker import run_in_background

_LOCAL = "__local__"
_NONE = "__none__"
_DEMO = "__demo__"
_CUSTOM = "__custom__"

# A "Try" applies a change to the running config and reverts it automatically
# unless kept. The client counts down TRY_SECONDS and normally performs the
# revert itself; the host-side timer (armed by actions.plan_try) sleeps
# TRY_SECONDS + TRY_GRACE so it only fires as a backup when the client is gone
# (e.g. the SSH connection dropped) — that extra margin lets the client win the
# race in the normal case, so the change is reverted exactly once.
TRY_SECONDS = 60
TRY_GRACE = 10

# A change can finish landing a beat after the command returns: an Apply that
# brings a link up then waits on a DHCP/RA lease, or a Save whose backend
# re-activates the link (nmcli con up / netplan apply) while IPv6 re-acquires via
# RA. A single probe can catch that mid-flight and then sit on the wrong state
# until a manual refresh, so every apply schedules a follow-up re-probe (Save,
# which bounces the link harder, gets a second, later one). See _reprobe_settling.
APPLY_SETTLE_MS = 1500
SAVE_SETTLE_MS = 1500

# A gentle background re-probe keeps the canvas tracking reality — a change still
# settling, or one made outside NetGrip — so the user never has to refresh by
# hand. It skips itself whenever it could interrupt (mid-gesture, a dialog/menu
# open, or a probe/apply already running), so it "doesn't break anything".
AUTO_REFRESH_MS = 10000


class _ClickableLabel(QLabel):
    """A status-bar label that emits ``clicked`` on a left press.

    The persistence indicator uses this for its one-click remediation. A QLabel
    rich-text ``<a>`` link / ``linkActivated`` proved unreliable as a status-bar
    permanent widget, so we handle the press directly and let the slot decide
    whether there is an action to run."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, initial_host: str | None = None, demo: bool = False):
        super().__init__()
        self.setWindowTitle("NetGrip")
        self.setWindowIcon(app_icon())
        self.resize(1100, 720)

        # Windows has no managed localhost: start unconnected and let the user
        # pick an SSH host instead of probing the local machine.
        self.runner: Runner = UnconnectedRunner() if IS_WINDOWS else LocalRunner()
        self.state: HostState | None = None
        self._busy = False
        # Links with runtime changes (Apply / Try-kept) not yet persisted via
        # Save. Names only; the config to write is re-derived from self.state at
        # Save time so it reflects current reality. Cleared on host switch.
        self._unsaved: set[str] = set()
        # (interface, family) the user has switched to Dynamic/DHCP but not saved
        # (M5). The static address stays at runtime until Save writes `dhcp`
        # through the backend; this drives the pending marker and the Save plan.
        self._dhcp_pending: set[tuple[str, int]] = set()
        # (interface, cidr) static addresses deleted but not yet saved. On a host
        # whose backend re-asserts its config (NetworkManager et al.) a runtime
        # `ip addr del` bounces straight back, so a delete there is a deferred
        # intent (like _dhcp_pending) applied at Save, not a runtime command.
        self._removed_addresses: set[tuple[str, str]] = set()
        # (interface, family) the user wants to stop taking DNS from the lease.
        # No runtime command exists for it (it's a backend/profile setting), so
        # like _dhcp_pending it is recorded here and applied at Save.
        self._dns_off_pending: set[tuple[str, int]] = set()
        # Per link: which link-layer properties (name/alias/MAC/MTU) were changed
        # at runtime but not yet persisted. Keyed by current name; Save writes a
        # systemd .link file carrying just these (persist_link). Separate from the
        # IP-config dirty set because .link files live beneath every backend.
        self._link_dirty: dict[str, set[str]] = {}
        # For a renamed link: current name → its boot/original name, so the .link
        # rule can match by OriginalName= the device reappears under at boot.
        self._link_origname: dict[str, str] = {}

        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.ip_dropped.connect(self._on_ip_dropped)
        self.canvas.ip_detached.connect(self._detach_ip)
        self.canvas.nic_dropped.connect(self._on_nic_dropped)
        self.canvas.vlan_dropped.connect(self._on_vlan_dropped)
        self.canvas.draft_vlan_dropped.connect(self._on_draft_vlan_dropped)
        self.canvas.node_menu_requested.connect(self._show_node_menu)
        self.canvas.region_menu_requested.connect(self._show_region_menu)
        self.canvas.canvas_menu_requested.connect(self._show_canvas_menu)

        # Floating Save affordance, pinned to the canvas's top-right corner. It
        # is hidden until there are unsaved changes the backend can persist, then
        # appears in the attention colour — Save is a real, reboot-affecting
        # commit, so it should be unmissable rather than a quiet toolbar entry.
        self.save_button = QPushButton(self.canvas)
        self.save_button.setStyleSheet(theme.save_button_style())
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.setShortcut(QKeySequence.StandardKey.Save)
        self.save_button.clicked.connect(self._save)
        self.save_button.hide()
        self.canvas.set_corner_widget(self.save_button)

        # Floating colour key, pinned top-left. Shown by default; the View menu
        # and a right-click "Hide legend" toggle it, and its visibility persists
        # (see _build_view_actions). _build_view_actions sets its initial state.
        self.legend = Legend(self.canvas)
        self.legend.hide_requested.connect(lambda: self.legend_action.setChecked(False))
        self.canvas.set_corner_widget(self.legend, "top-left")

        # Background re-probe so the canvas stays current on its own (see
        # AUTO_REFRESH_MS). Self-guards against interrupting the user.
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(AUTO_REFRESH_MS)
        self._auto_timer.timeout.connect(self._auto_reprobe)
        self._auto_timer.start()

        self._build_toolbar()
        # Persistent persistence indicator on the right of the status bar: which
        # subsystem owns config on the current host and whether changes survive a
        # reboot. Stays put while transient messages scroll on the left.
        self._backend_label = _ClickableLabel()
        self._backend_label.setContentsMargins(0, 0, 8, 0)
        # Runtime-only hosts that could gain a backend make the indicator
        # clickable; clicking runs the remediation (see _update_backend_indicator
        # / _on_backend_clicked).
        self._backend_label.clicked.connect(self._on_backend_clicked)
        self.statusBar().addPermanentWidget(self._backend_label)
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
    def _build_view_actions(self) -> None:
        """The canvas view toggles (checkable, persisted in QSettings). They live
        in the toolbar's *View* dropdown rather than a menubar, so the window has
        no separate menubar row of its own."""
        # Loopback has its own toggle (the rest of the canvas is filtered by
        # Hide offline); both persist across sessions.
        self.loopback_action = QAction("Show loopback", self)
        self.loopback_action.setCheckable(True)
        self.loopback_action.setChecked(QSettings().value("show_loopback", False, type=bool))
        self.loopback_action.toggled.connect(self._loopback_toggled)

        self.hide_offline_action = QAction("Hide offline", self)
        self.hide_offline_action.setCheckable(True)
        self.hide_offline_action.setChecked(QSettings().value("hide_offline", False, type=bool))
        self.hide_offline_action.toggled.connect(self._hide_offline_toggled)

        # A container's two L3 lines, each independently hideable (a busy Docker
        # host can have many). Both default on; see canvas RouteEdge.
        self.forwards_action = QAction("Show published ports", self)
        self.forwards_action.setCheckable(True)
        self.forwards_action.setChecked(QSettings().value("show_forwards", True, type=bool))
        self.forwards_action.toggled.connect(self._forwards_toggled)

        self.egress_action = QAction("Show default routes", self)
        self.egress_action.setCheckable(True)
        self.egress_action.setChecked(QSettings().value("show_egress", True, type=bool))
        self.egress_action.toggled.connect(self._egress_toggled)

        # The floating colour key (legend.py). Toggling drives its visibility
        # directly — it floats over the canvas, so no repopulate is needed.
        self.legend_action = QAction("Legend", self)
        self.legend_action.setCheckable(True)
        self.legend_action.setChecked(QSettings().value("legend_visible", True, type=bool))
        self.legend_action.toggled.connect(self._legend_toggled)
        self.legend.setVisible(self.legend_action.isChecked())

    def _build_toolbar(self) -> None:
        self._build_view_actions()
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        bar.addWidget(QLabel(" Host: "))
        self.host_combo = QComboBox()
        self.host_combo.setMinimumWidth(220)
        if IS_WINDOWS:
            self.host_combo.addItem("Select a host…", _NONE)
        else:
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

        # File menu (export, with room for future file actions). A dropdown
        # button mirroring View/Help, since the window has no menubar.
        file_button = QToolButton()
        file_button.setText("File")
        file_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        file_menu = QMenu(file_button)
        file_menu.addAction("Export diagram…", self._export_diagram)
        file_button.setMenu(file_menu)
        bar.addWidget(file_button)

        # Refresh as an icon (with a text fallback where the theme has no icon);
        # the F5 shortcut surfaces in the tooltip since the label is hidden.
        refresh = QAction(self)
        icon = QIcon.fromTheme("view-refresh")
        if icon.isNull():
            refresh.setText("Refresh")
        else:
            refresh.setIcon(icon)
        refresh.setShortcut(QKeySequence(Qt.Key.Key_F5))
        refresh.setToolTip("Refresh (F5)")
        refresh.triggered.connect(self.refresh)
        bar.addAction(refresh)

        fit = QAction("Fit view", self)
        fit.triggered.connect(self.canvas.fit_all)
        bar.addAction(fit)

        # View toggles grouped under one dropdown (no menubar; see
        # _build_view_actions).
        view_button = QToolButton()
        view_button.setText("View")
        view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        view_menu = QMenu(view_button)
        view_menu.addAction(self.legend_action)
        view_menu.addAction(self.loopback_action)
        view_menu.addAction(self.hide_offline_action)
        view_menu.addSeparator()
        view_menu.addAction(self.forwards_action)
        view_menu.addAction(self.egress_action)
        view_button.setMenu(view_menu)
        bar.addWidget(view_button)

        relayout = QAction("Auto-layout", self)
        relayout.triggered.connect(self.canvas.auto_layout)
        bar.addAction(relayout)

        bar.addSeparator()
        bar.addWidget(QLabel(" Theme: "))
        self.theme_combo = QComboBox()
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme_combo.addItem(label, value)
        saved = QSettings().value("theme", "system")
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(saved if saved in
                ("system", "light", "dark") else "system"))
        )
        self.theme_combo.activated.connect(self._theme_picked)
        bar.addWidget(self.theme_combo)

        # Push Help to the far right with an expanding spacer, then a ``?`` menu
        # button (a menu opening a dialog is fine; hard rule 5 only forbids a
        # dialog opening another dialog).
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setToolTip("Help")
        self.help_button.setStyleSheet(theme.help_button_style())
        self.help_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        help_menu = QMenu(self.help_button)
        help_menu.addAction("About NetGrip", self._about)
        self.help_button.setMenu(help_menu)
        bar.addWidget(self.help_button)

    def _export_diagram(self) -> None:
        """Save the current diagram, exactly as shown, to an SVG or PDF file."""
        if self.canvas.scene().itemsBoundingRect().isEmpty():
            QMessageBox.information(
                self, "Export diagram", "There's nothing on the canvas to export yet."
            )
            return
        host = getattr(self.canvas, "_host_label", None) or "netgrip"
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in host)
        path, selected = QFileDialog.getSaveFileName(
            self, "Export diagram", f"{safe}-network.svg",
            "SVG image (*.svg);;PDF document (*.pdf)",
        )
        if not path:
            return
        lower = path.lower()
        if lower.endswith(".pdf"):
            fmt = "pdf"
        elif lower.endswith(".svg"):
            fmt = "svg"
        else:  # no extension typed — take it from the chosen filter
            fmt = "pdf" if "pdf" in selected.lower() else "svg"
            path += f".{fmt}"
        try:
            ok = self.canvas.export_diagram(path, fmt)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        if not ok:
            QMessageBox.information(
                self, "Export diagram", "There's nothing on the canvas to export yet."
            )

    def _loopback_toggled(self, checked: bool) -> None:
        QSettings().setValue("show_loopback", checked)
        self._repopulate()

    def _hide_offline_toggled(self, checked: bool) -> None:
        QSettings().setValue("hide_offline", checked)
        self._repopulate()

    def _forwards_toggled(self, checked: bool) -> None:
        QSettings().setValue("show_forwards", checked)
        self._repopulate()

    def _egress_toggled(self, checked: bool) -> None:
        QSettings().setValue("show_egress", checked)
        self._repopulate()

    def _legend_toggled(self, checked: bool) -> None:
        QSettings().setValue("legend_visible", checked)
        self.legend.setVisible(checked)
        if checked:
            self.canvas.position_corner_widget("top-left")
            self.legend.raise_()

    def _repopulate(self) -> None:
        """Redraw the canvas with the current view options. The single funnel for
        every populate call so loopback/offline toggles stay in one place."""
        self.canvas.populate(
            self.state,
            self.loopback_action.isChecked(),
            self.hide_offline_action.isChecked(),
            self.forwards_action.isChecked(),
            self.egress_action.isChecked(),
        )

    def _theme_picked(self, index: int) -> None:
        mode = self.theme_combo.itemData(index)
        QSettings().setValue("theme", mode)
        theme.apply_theme(QApplication.instance(), mode)
        self.save_button.setStyleSheet(theme.save_button_style())  # follow the scheme
        self.help_button.setStyleSheet(theme.help_button_style())
        self.legend.apply_theme()  # re-tint swatches to the new scheme
        # Repaint the canvas (node colours) under the new scheme.
        self._repopulate()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About NetGrip",
            f"<b>NetGrip {netgrip.__version__}</b><br>"
            "Visual, drag-and-drop network interface manager.<br>"
            "Changes are applied to the running system with iproute2, then "
            "<b>Save</b> persists them across reboots through your host's "
            "network backend (netplan, systemd-networkd, NetworkManager or "
            "ifupdown).<br><br>"
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
        elif data == _NONE:
            self._connect_to(UnconnectedRunner())
        elif data:
            self._connect_to(SSHRunner(data))

    def _connect_to(self, runner: Runner) -> None:
        self.runner.close()
        self.runner = runner
        # Unsaved changes belong to the host we are leaving; the new host starts
        # clean (its own runtime state is whatever the probe reports).
        self._unsaved.clear()
        self._dhcp_pending.clear()
        self._removed_addresses.clear()
        self._dns_off_pending.clear()
        self._link_dirty.clear()
        self._link_origname.clear()
        self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        runner = self.runner
        if isinstance(runner, UnconnectedRunner):
            self.state = None
            self._repopulate()
            self._update_backend_indicator(None)
            self.statusBar().showMessage("Select a host to connect over SSH.")
            return
        if isinstance(runner, DemoRunner):
            interfaces = demo_interfaces()
            docker_networks, containers = demo_docker()
            apply_docker(interfaces, docker_networks)  # tag docker0 / br-… bridges
            self._set_state(interfaces, DEMO_DNS, DEMO_DNS_SEARCH,
                            can_edit_dns=False, backend=DEMO_BACKEND,
                            docker_networks=docker_networks, containers=containers)
            return
        self._set_busy(True, f"Reading interfaces on {runner.label}…")

        def work() -> tuple:
            interfaces = probe(runner)
            servers, search, can_edit, per_link = probe_dns(runner)
            apply_link_dns(interfaces, per_link)  # per-link DNS onto each group
            docker_networks, containers = probe_docker(runner)  # best-effort
            apply_docker(interfaces, docker_networks)  # tag bridges with their net
            backend = detect_backend(runner)  # which subsystem owns persistent config
            return interfaces, servers, search, can_edit, backend, docker_networks, containers

        run_in_background(
            work,
            on_done=lambda res: (self._set_busy(False), self._set_state(*res)),
            on_error=lambda msg: (self._set_busy(False), self._on_refresh_error(msg)),
        )

    def _on_refresh_error(self, message: str) -> None:
        runner = self.runner
        kind = hostkey_failure(message) if isinstance(runner, SSHRunner) else None
        # Only offer once: if we already relaxed the policy and it still failed,
        # show the real error rather than looping on the same dialog.
        if kind and runner.hostkey_policy == SSHRunner.HOSTKEY_STRICT:
            self._offer_accept_hostkey(runner, kind, message)
            return
        if isinstance(runner, SSHRunner) and is_auth_failure(message):
            self._prompt_password(runner)
            return
        self._show_error(message)

    def _prompt_password(self, runner: SSHRunner) -> None:
        # Re-prompt on a failed attempt; an empty entry / Cancel gives up.
        retry = runner.had_password()
        self.statusBar().showMessage("Authentication required")
        prompt = (
            "Password was not accepted. Try again:"
            if retry
            else f"Password for {runner.host}:"
        )
        password, ok = QInputDialog.getText(
            self, "SSH password", prompt, QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            runner.set_password(None)
            self.statusBar().showMessage("Not connected")
            return
        runner.set_password(password)
        self.refresh()

    def _offer_accept_hostkey(self, runner: SSHRunner, kind: str, message: str) -> None:
        if kind == "changed":
            self.statusBar().showMessage("Host key changed")
            title = "Host key changed"
            text = (
                f"WARNING: the host key for “{runner.host}” is different from the "
                "one saved in your known_hosts file.\n\n"
                "This is normal if the machine was reinstalled or its address was "
                "reused — but it can also mean someone is impersonating the host "
                "(a man-in-the-middle attack).\n\n"
                "Connect anyway? The old key will be replaced with the new one."
            )
        else:
            self.statusBar().showMessage("Unknown host key")
            title = "Unknown host"
            text = (
                f"The authenticity of host “{runner.host}” can't be established — "
                "its key isn't in your known_hosts file.\n\n"
                "Connect anyway and remember this host's key?"
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        connect = box.addButton("Connect anyway", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not connect:
            return
        # A changed key won't be trusted (and ssh disables password auth) while
        # the stale entry lingers, so drop it; accept-new then re-learns the key.
        if kind == "changed":
            runner.forget_hostkey(message)
        runner.hostkey_policy = SSHRunner.HOSTKEY_ACCEPT_NEW
        self.refresh()

    def _set_state(self, interfaces: list[Interface], dns: list[str] | None = None,
                   dns_search: list[str] | None = None, can_edit_dns: bool = False,
                   backend: Backend | None = None,
                   docker_networks: list[DockerNetwork] | None = None,
                   containers: list[Container] | None = None) -> None:
        self.state = HostState(
            self.runner.label, interfaces,
            list(dns or []), list(dns_search or []), can_edit_dns,
            backend=backend,
            docker_networks=list(docker_networks or []),
            containers=list(containers or []),
        )
        # Re-attach any unsaved "→ DHCP" intents, dropping ones whose link or
        # static address is gone (already DHCP, or removed), so a stale pending
        # marker never lingers after the switch actually lands.
        self._dhcp_pending = {
            (name, fam) for (name, fam) in self._dhcp_pending
            if (iface := self.state.get(name)) is not None
            and any(not a.dynamic and a.scope == "global" for a in iface.addresses_for(fam))
        }
        self.state.dhcp_pending = set(self._dhcp_pending)
        # Re-attach pending deletes, dropping any whose address is already gone
        # (the delete landed, or the link vanished) so no stale marker lingers.
        self._removed_addresses = {
            (name, cidr) for (name, cidr) in self._removed_addresses
            if (iface := self.state.get(name)) is not None
            and any(a.cidr == cidr for a in iface.addresses)
        }
        self.state.removed_pending = set(self._removed_addresses)
        # Re-attach "ignore DHCP DNS" intents, dropping any whose link is gone or
        # whose family neither takes a lease nor is pending a switch to one (so
        # there is nothing to ignore). The pending-switch case matters when the
        # user chose DHCP + ignore together: the family is still static until Save.
        self._dns_off_pending = {
            (name, fam) for (name, fam) in self._dns_off_pending
            if (iface := self.state.get(name)) is not None
            and (iface.uses_dhcp(fam) or (name, fam) in self._dhcp_pending)
        }
        self.state.dns_off_pending = set(self._dns_off_pending)
        # Drop link-layer dirtiness for links that have vanished (deleted, or a
        # rename whose old name is gone) so a stale .link entry never lingers.
        present = self.state.link_names()
        self._link_dirty = {n: k for n, k in self._link_dirty.items() if n in present}
        self._link_origname = {n: o for n, o in self._link_origname.items() if n in present}
        self._repopulate()
        self._update_backend_indicator(backend)
        self._update_save_button()  # backend (and so write-ability) may have changed
        dns_note = f" · DNS {', '.join(self.state.dns)}" if self.state.dns else ""
        self.statusBar().showMessage(
            f"{self.runner.label}: {len(interfaces)} interfaces{dns_note}"
        )

    def _update_backend_indicator(self, backend: Backend | None) -> None:
        """Reflect the host's config owner (and whether Save can persist) in the
        status-bar indicator. A managed host shows its backend in the dim text
        colour; a runtime-only/unknown host is flagged in the warning colour,
        since a change there will not survive a reboot."""
        if backend is None:
            self._backend_label.clear()
            self._backend_label.setToolTip("")
            self._backend_label.setStyleSheet("")
            self._backend_label.setCursor(Qt.CursorShape.ArrowCursor)
            return
        colour = theme.text_dim() if backend.persists else theme.error()
        note = (
            "Save will write persistent configuration through this backend."
            if backend.persists
            else "Changes apply at runtime only and are lost on reboot."
        )
        self._backend_label.setText(f"Persist: {backend.label}")
        # Style the label's own text through its palette + font, NOT a stylesheet:
        # a `color` / `text-decoration` stylesheet on a QLabel bleeds into the
        # label's tooltip, painting the whole tooltip red and underlined. Palette
        # (WindowText) and font underline affect only the label text.
        palette = self._backend_label.palette()
        palette.setColor(QPalette.ColorRole.WindowText, colour)
        self._backend_label.setPalette(palette)
        font = self._backend_label.font()
        # Underline + hand cursor mark the indicator as clickable when there's a
        # one-click remediation (install ifupdown2 on a runtime-only ifupdown host).
        font.setUnderline(backend.install_ifupdown2)
        self._backend_label.setFont(font)
        if backend.install_ifupdown2:
            self._backend_label.setCursor(Qt.CursorShape.PointingHandCursor)
            note += "\n\nClick to install ifupdown2 and enable persistent configuration."
        else:
            self._backend_label.setCursor(Qt.CursorShape.ArrowCursor)
        self._backend_label.setToolTip(f"{backend.summary}\n\n{note}")

    def _on_backend_clicked(self) -> None:
        """Handle a click on the persistence indicator.

        Only acts when the current host is a runtime-only ifupdown box that
        ifupdown2 would make writable; then it installs it through the normal
        ``_apply`` path — the same confirm → elevate → run → re-probe flow every
        networking change uses, so escalation prompts (and caches) identically.
        The post-apply re-probe re-detects the backend, so the indicator updates
        itself once the package is in."""
        backend = self.state.backend if self.state else None
        if backend and backend.install_ifupdown2:
            self._apply(
                "Install ifupdown2 (enable persistent configuration)",
                actions.plan_install_ifupdown2(),
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
    def _apply(self, title: str, plan: list[list[str]], on_success=None,
               *, revert: list[list[str]] | None = None, settle_ms: int = 0) -> None:
        """Confirm and run a plan. When a ``revert`` (inverse) plan is supplied
        the confirmation offers *Try* — apply now and auto-revert shortly unless
        kept — alongside *Apply*. Try is for the connection-risky gestures where
        a wrong value could lock you out; its provisional change does not run
        ``on_success`` (that side effect belongs to a committed change).

        ``settle_ms`` delays the post-run re-probe (used by Save, whose backend
        reconfigure briefly disrupts links/DNS) so the canvas redraws the settled
        state once instead of flashing the transient."""
        if self._busy or not plan:
            return
        choice = confirm_commands(
            self, title, plan, self.runner.label,
            allow_try=bool(revert), try_seconds=TRY_SECONDS,
        )
        if choice == CONFIRM_CANCEL:
            return
        if not self._ensure_escalation():
            return
        if choice == CONFIRM_TRY:
            self._try(title, plan, revert, on_keep=on_success)
            return
        runner = self.runner
        self._set_busy(True, f"{title}…")

        def done(_result) -> None:
            self._set_busy(False)
            self.statusBar().showMessage(f"{title}: done")
            self._mark_unsaved(plan)
            if on_success:
                on_success()
            self._reprobe_settling(settle_ms)

        run_in_background(
            lambda: runner.run_privileged(plan),
            on_done=done,
            on_error=self._on_privileged_error,
        )

    def _ensure_escalation(self) -> bool:
        """Before a privileged run, make sure we can become root on the target —
        prompting once for a sudo password (then cached for the session) instead
        of being prompted on every action. Covers both the *local* machine
        (LocalRunner) and a *remote* host over SSH (SSHRunner); demo passes
        through. Returns False, so the caller aborts, on cancel or when root is
        unreachable."""
        runner = self.runner
        if not isinstance(runner, (LocalRunner, SSHRunner)):
            return True
        status = runner.escalation_status()
        if status == "ready":
            return True
        if status == "unavailable":
            self._show_error(
                "NetGrip can't gain administrator rights on this machine. Run it "
                "as root, set up passwordless sudo, or install polkit (pkexec)."
                if isinstance(runner, LocalRunner)
                else f"NetGrip can't gain root on {runner.label}: you are not a "
                "sudoer there (or sudo isn't installed). Log in as root or grant "
                "your user sudo access."
            )
            return False
        return self._prompt_sudo_password(runner)

    def _prompt_sudo_password(self, runner: LocalRunner | SSHRunner) -> bool:
        password, ok = QInputDialog.getText(
            self, "Authentication required",
            f"Administrator (sudo) password for {runner.label}:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not password:
            return False
        # On SSH the sudo password is distinct from the login password, so it has
        # its own setter; locally the one cached password drives sudo.
        if isinstance(runner, SSHRunner):
            runner.set_sudo_password(password)
        else:
            runner.set_password(password)
        return True

    def _on_privileged_error(self, message: str) -> None:
        """Error handler for a privileged run. A wrong cached sudo password is
        cleared (so the next attempt re-prompts) rather than looping on it."""
        self._set_busy(False)
        runner = self.runner
        if isinstance(runner, LocalRunner) and sudo_auth_failed(message):
            runner.set_password(None)
            self._show_error("Incorrect administrator password. Please retry the action.")
        elif isinstance(runner, SSHRunner) and sudo_auth_failed(message):
            runner.set_sudo_password(None)
            self._show_error("Incorrect sudo password. Please retry the action.")
        else:
            self._show_error(message)

    def _reprobe_settling(self, settle_ms: int) -> None:
        """Re-probe so the canvas converges on the *settled* state by itself.

        A change can finish landing a beat after its command returns — an Apply
        that brings a link up then waits on a DHCP/RA lease, or a Save whose
        backend re-activates the link while IPv6 re-acquires via RA — so a single
        probe can catch it mid-flight. An Apply shows the result now and re-probes
        once shortly after; a Save (which bounces the link harder) probes after
        the settle and again well after. The background poll is the final net."""
        if settle_ms:
            QTimer.singleShot(settle_ms, self.refresh)
            QTimer.singleShot(settle_ms + 4500, self.refresh)
        else:
            self.refresh()
            QTimer.singleShot(APPLY_SETTLE_MS, self.refresh)

    def _auto_reprobe(self) -> None:
        """Background-timer tick: re-probe the host, but only when it can't
        interrupt anything. Skipped while a probe/apply is running, while the
        user is mid-gesture (a mouse button is down — repopulating would delete
        the dragged box), and while any dialog or context menu is open."""
        if self._busy or isinstance(self.runner, (UnconnectedRunner, DemoRunner)):
            return
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        self.refresh()

    def _try(self, title: str, plan: list[list[str]], revert: list[list[str]],
             on_keep=None) -> None:
        """Apply ``plan`` with an armed host-side auto-revert, then open the
        Keep/Revert countdown once the change is on the running config. A kept
        change runs ``on_keep`` (the committed-only side effect, e.g. naming the
        new address); a reverted one does not."""
        runner = self.runner
        token = secrets.token_hex(8)
        arm = actions.plan_try(plan, revert, token, timeout=TRY_SECONDS + TRY_GRACE)
        self._set_busy(True, f"{title} (try)…")

        def armed(_result) -> None:
            self._set_busy(False)
            self.refresh()  # show the just-applied change before the user decides
            self._start_try_countdown(title, token, plan, revert, on_keep)

        run_in_background(
            lambda: runner.run_privileged(arm),
            on_done=armed,
            on_error=self._on_privileged_error,
        )

    def _start_try_countdown(self, title: str, token: str, plan: list[list[str]],
                             revert: list[list[str]], on_keep=None) -> None:
        dlg = TryCountdownDialog(self, title, TRY_SECONDS)
        dlg.exec()
        # Keep just disarms; anything else (Revert now, timeout, closing) reverts
        # immediately. Both also clear the host-side sentinel so it can't double-
        # fire later. The client owns the decision; the host timer is the backup.
        if dlg.kept:
            finishing, verb = actions.plan_keep(token), "kept"
        else:
            finishing, verb = actions.plan_revert_now(token, revert), "reverted"
        runner = self.runner
        self._set_busy(True, f"{title}: {verb}…")

        def done(_r) -> None:
            self._set_busy(False)
            self.statusBar().showMessage(f"{title}: {verb}")
            if dlg.kept:
                # A kept change is a real runtime edit now — same as Apply, it
                # becomes unsaved until persisted. A revert leaves nothing dirty.
                self._mark_unsaved(plan)
                if on_keep:
                    on_keep()
            self.refresh()

        run_in_background(
            lambda: runner.run_privileged(finishing),
            on_done=done,
            on_error=self._on_privileged_error,
        )

    # ------------------------------------------------------------------ #
    # Save: persist unsaved runtime changes through the backend
    # ------------------------------------------------------------------ #
    def _mark_unsaved(self, plan: list[list[str]]) -> None:
        """Record the links a just-applied plan touched as unsaved. ``lo`` is
        excluded — loopback isn't something Save persists."""
        self._unsaved |= actions.affected_links(plan) - {"lo"}
        self._update_save_button()

    def _record_link_props(self, old_name: str, new_name: str, keys: set[str],
                           boot_name: str) -> None:
        """Record changed link-layer properties (name/alias/MAC/MTU) as unsaved,
        so Save writes a ``.link`` file for them (persist_link). Runs only on a
        committed change (Apply / Try-kept), like the IP-config dirty marker.

        Keyed by the link's *new* name (a rename moves the entry, and drops the
        stale old name a rename plan also marked unsaved); ``boot_name`` is the
        device's original name, kept for the ``.link`` ``OriginalName=`` match."""
        if not keys:
            return
        self._link_dirty[new_name] = self._link_dirty.pop(old_name, set()) | keys
        prior = self._link_origname.pop(old_name, boot_name)
        if new_name != prior:
            self._link_origname[new_name] = prior
        self._unsaved.add(new_name)
        if old_name != new_name:
            self._unsaved.discard(old_name)
        self._update_save_button()

    def _set_dhcp_intent(self, name: str, family: int) -> None:
        """Record a pending switch of (name, family) to DHCP (M5). No runtime
        change: the static stays until Save writes `dhcp` and the backend reload
        performs the swap. Marks the link unsaved and redraws the box's marker."""
        self._dhcp_pending.add((name, family))
        self._unsaved.add(name)
        self._update_save_button()
        if self.state:
            self.state.dhcp_pending = set(self._dhcp_pending)
            self._repopulate()
        self.statusBar().showMessage(f"{name} IPv{family} will switch to DHCP when you Save.")

    def _set_dns_off_intent(self, name: str, family: int, ignore: bool) -> None:
        """Record (or clear) the intent to stop taking DNS from the lease for
        (name, family). No runtime change — applied at Save through the backend's
        ignore-auto-dns. Marks the link unsaved when set; the caller redraws."""
        key = (name, family)
        if ignore == (key in self._dns_off_pending):
            return  # nothing changed
        if ignore:
            self._dns_off_pending.add(key)
            self._unsaved.add(name)
        else:
            self._dns_off_pending.discard(key)
        self._update_save_button()
        if self.state:
            self.state.dns_off_pending = set(self._dns_off_pending)

    def _update_save_button(self) -> None:
        """Show the floating Save button only when there is something to persist
        *and* the backend can write it; otherwise hide it entirely."""
        count = len(self._unsaved)
        backend = self.state.backend if self.state else None
        can_write = bool(backend and backend.persists)
        if count and can_write:
            plural = "s" if count != 1 else ""
            self.save_button.setText(f"Save {count} change{plural}")
            self.save_button.setToolTip(
                f"Persist {count} changed link{plural} through {backend.label} "
                "— survives reboot"
            )
            self.save_button.adjustSize()
            self.canvas.position_corner_widget()
            self.save_button.show()
            self.save_button.raise_()
        else:
            self.save_button.hide()

    def _save(self) -> None:
        """Persist every unsaved link's current IP config through the backend.

        Save writes the *running* config of the touched links (declarative), not
        a replay of the deltas — so it captures wherever Apply/Try-keep left
        them. It reuses the standard confirm → escalate → run → re-probe path
        (Apply-only; persisting is itself the commit, so no Try)."""
        if self._busy or not self.state or not self._unsaved:
            return
        backend = self.state.backend
        if backend is None or not backend.persists:
            return
        links = sorted(self._unsaved)
        configs = []
        for name in links:
            iface = self.state.get(name)
            if iface is None:
                continue
            cfg = persist.link_config(iface)
            # Apply any pending "→ DHCP" switch for this link's families (M5):
            # the running state still holds the static, so override it here.
            for fam in (4, 6):
                if (name, fam) in self._dhcp_pending:
                    cfg.set_dhcp(fam)
                if (name, fam) in self._dns_off_pending:
                    cfg.set_ignore_dhcp_dns(fam)
            # Apply pending deletes: the address is still on the link (a managed
            # backend would revert a runtime del), so drop it from the config the
            # profile will be rewritten to.
            for (rn, cidr) in self._removed_addresses:
                if rn == name:
                    cfg.remove_address(cidr)
            configs.append(cfg)
        if not configs:
            self._unsaved.clear()  # the links are gone (deleted); nothing to save
            self._update_save_button()
            return
        # Link-layer changes (name/alias/MAC/MTU) persist via systemd .link files
        # beneath the backend, so the same Save carries them (persist_link).
        link_plan = self._link_persist_plan(links)
        saved = set(links)
        title = f"Save {len(configs)} link(s) to {backend.label}"
        # NetworkManager needs each device's connection profile resolved first;
        # read that off-thread, then build and confirm the plan.
        if backend.kind == persist.NETWORKMANAGER:
            self._save_via_nm(title, configs, saved, backend, link_plan)
            return
        try:
            plan = persist.persist_plan(configs, backend)
        except persist.PersistError as exc:
            self._show_error(str(exc))
            return
        self._apply(title, plan + link_plan, on_success=lambda: self._clear_saved(saved),
                    settle_ms=SAVE_SETTLE_MS)

    def _link_persist_plan(self, links: list[str]) -> list[list[str]]:
        """The systemd ``.link`` write-through for any of ``links`` carrying
        unsaved link-layer changes (name/alias/MAC/MTU); empty when none do."""
        props = []
        for name in links:
            keys = self._link_dirty.get(name)
            iface = self.state.get(name) if self.state else None
            if keys and iface is not None:
                match = self._link_origname.get(name, name)
                props.append(persist_link.link_props(iface, keys, match))
        return persist_link.plan_link_files(props)

    def _save_via_nm(self, title: str, configs: list[persist.LinkConfig],
                     saved: set[str], backend: Backend,
                     link_plan: list[list[str]]) -> None:
        runner = self.runner
        self._set_busy(True, "Reading NetworkManager connections…")

        def done(connections: dict[str, str]) -> None:
            self._set_busy(False)
            try:
                plan = persist.persist_plan(configs, backend, connections)
            except persist.PersistError as exc:
                self._show_error(str(exc))
                return
            plan += link_plan  # .link files persist beneath NM too
            if not plan:  # nothing nmcli or .link can express for these links
                self._clear_saved(saved)
                return
            self._apply(title, plan, on_success=lambda: self._clear_saved(saved),
                        settle_ms=SAVE_SETTLE_MS)

        run_in_background(
            lambda: persist.read_nm_connections(runner),
            on_done=done,
            on_error=lambda msg: (self._set_busy(False), self._show_error(msg)),
        )

    def _clear_saved(self, links: set[str]) -> None:
        self._unsaved -= links
        # A saved link's pending DHCP switch is now persisted; drop the intent
        # (the post-Save re-probe will show the lease in place of the static).
        self._dhcp_pending = {(n, f) for (n, f) in self._dhcp_pending if n not in links}
        self._removed_addresses = {(n, c) for (n, c) in self._removed_addresses
                                   if n not in links}
        self._dns_off_pending = {(n, f) for (n, f) in self._dns_off_pending if n not in links}
        # The .link file is written; drop the link-layer dirtiness (and its
        # remembered boot name) for the saved links.
        self._link_dirty = {n: k for n, k in self._link_dirty.items() if n not in links}
        self._link_origname = {n: o for n, o in self._link_origname.items() if n not in links}
        self._update_save_button()

    # ------------------------------------------------------------------ #
    # drag-and-drop gestures
    # ------------------------------------------------------------------ #
    def _on_ip_dropped(self, node: IpNode, target, clone: bool) -> None:
        target_name = target.iface.name
        # Docker owns its bridges' addressing and its containers' IPs; refuse to
        # add/move/clone an address onto (or away from) docker-managed config.
        if self._docker_owned(target.iface):
            self._refuse_docker(target_name)
            return
        if not node.is_draft and self._docker_owned(node.parent_name):
            self._refuse_docker(node.parent_name)
            return
        if node.is_draft:
            self._attach_draft(node, target_name)
        elif clone:
            self._apply(
                f"Clone IPv{node.family} config to {target_name}",
                actions.plan_add_addresses(target_name, [node.cidr]),
                revert=actions.plan_remove_addresses(target_name, [node.cidr]),
            )
        else:
            self._apply(
                f"Move IPv{node.family} config from {node.parent_name} to {target_name}",
                actions.plan_move_addresses(node.parent_name, target_name, [node.cidr]),
                # Inverse: take it off the target and restore it where it came
                # from (restore, not add, so a re-leased address won't error).
                revert=(actions.plan_remove_addresses(target_name, [node.cidr])
                        + actions.plan_restore_addresses(node.parent_name, [node.cidr])),
            )

    def _on_nic_dropped(self, node: NicNode, target) -> None:
        nic = node.iface.name
        if self._docker_owned(target.iface):
            self._refuse_docker(target.iface.name)
            return
        if isinstance(target, GroupNode):
            self._apply(
                f"Add {nic} to {target.iface.name}",
                actions.plan_add_member(target.iface.name, nic),
                revert=actions.plan_remove_member(nic),
            )
        else:
            self._new_bond_dialog(preselected=[nic, target.iface.name])

    def _on_vlan_dropped(self, node: VlanNode, target) -> None:
        new_parent = target.iface.name
        if self._docker_owned(target.iface):
            self._refuse_docker(new_parent)
            return
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

    def _new_bridge_dialog(self, preselected: list[str] | None = None) -> None:
        """Create a new bridge, optionally adding a NIC as the first member."""
        if not self.state:
            return
        dialog = BridgeDialog(self, self.state.link_names())
        if not dialog.exec():
            return
        plan = actions.plan_create_bridge(dialog.name, dialog.vlan_aware)
        if preselected:
            # Add the pre-selected NICs as members of the new bridge.
            for nic_name in preselected:
                plan += actions.plan_add_member(dialog.name, nic_name)
        self._apply(f"Create bridge {dialog.name}", plan,
                    revert=actions.plan_delete_link(dialog.name))

    def _bridge_vlan_port_dialog(self, member: Interface) -> None:
        """Configure VLAN membership for one port of a vlan-aware bridge."""
        dlg = BridgeVlanPortDialog(
            self, member.name, member.pvid, member.vlan_tags
        )
        if not dlg.exec():
            return
        plan: list[list[str]] = []
        revert: list[list[str]] = []
        # pvid change
        if dlg.clear_pvid and member.pvid is not None:
            plan += actions.plan_bridge_vlan_del(member.name, member.pvid)
            revert += actions.plan_bridge_vlan_add(
                member.name, member.pvid, pvid=True, tagged=False
            )
        elif dlg.new_pvid is not None:
            plan += actions.plan_bridge_vlan_add(
                member.name, dlg.new_pvid, pvid=True, tagged=False
            )
            revert += actions.plan_bridge_vlan_del(member.name, dlg.new_pvid)
            if member.pvid is not None and member.pvid != dlg.new_pvid:
                plan += actions.plan_bridge_vlan_del(member.name, member.pvid)
                revert += actions.plan_bridge_vlan_add(
                    member.name, member.pvid, pvid=True, tagged=False
                )
        # tagged VLAN changes
        for vid in dlg.add_tagged:
            plan += actions.plan_bridge_vlan_add(member.name, vid, tagged=True)
            revert += actions.plan_bridge_vlan_del(member.name, vid)
        for vid in dlg.del_tagged:
            plan += actions.plan_bridge_vlan_del(member.name, vid)
            revert += actions.plan_bridge_vlan_add(member.name, vid, tagged=True)
        if not plan:
            return  # nothing changed
        self._apply(
            f"Configure VLAN membership for {member.name}",
            plan,
            revert=revert if revert else None,
        )

    # ------------------------------------------------------------------ #
    # context menus
    # ------------------------------------------------------------------ #
    def _show_node_menu(self, node, global_pos: QPoint) -> None:
        if not self.state:
            return
        menu = QMenu(self)
        if isinstance(node, SystemDns):
            self._fill_dns_menu(menu)
        elif isinstance(node, IpNode):
            self._fill_ip_menu(menu, node)
        elif isinstance(node, GroupNode):
            if self._docker_owned(node.iface):
                self._fill_docker_readonly_menu(menu, node.iface)
            else:
                self._fill_group_menu(menu, node.iface)
        elif isinstance(node, VlanNode):
            self._fill_vlan_menu(menu, node.iface)
        elif isinstance(node, DraftVlanNode):
            self._fill_draft_vlan_menu(menu, node)
        elif isinstance(node, NicNode):
            self._fill_nic_menu(menu, node.iface)
        if not menu.isEmpty():
            menu.exec(global_pos)

    def _docker_owned(self, iface_or_name) -> bool:
        """Whether a link (by Interface or name) is docker-managed, hence shown
        read-only — see HostState.is_docker_owned."""
        if not self.state:
            return False
        iface = (iface_or_name if isinstance(iface_or_name, Interface)
                 else self.state.get(iface_or_name))
        return bool(iface and self.state.is_docker_owned(iface))

    def _refuse_docker(self, name: str) -> None:
        """Bounce a mutating gesture aimed at docker-owned config: explain why
        and redraw so any dragged box snaps back to where it was."""
        self.statusBar().showMessage(
            f"{name} is managed by Docker — edit it with docker / compose, not here."
        )
        self._repopulate()

    def _fill_docker_readonly_menu(self, menu: QMenu, iface: Interface) -> None:
        net = iface.docker_network or (
            self.state.docker_network_for_bridge(iface.master).name
            if iface.master and self.state.docker_network_for_bridge(iface.master) else None
        )
        header = f"Managed by Docker ({net})" if net else "Managed by Docker"
        menu.addAction(header).setEnabled(False)
        menu.addAction("Read-only here — edit with docker / compose").setEnabled(False)

    def _show_region_menu(self, group: IpGroup, global_pos: QPoint) -> None:
        if not self.state:
            return
        iface, family = group.iface, group.family
        menu = QMenu(self)
        if self._docker_owned(iface):
            self._fill_docker_readonly_menu(menu, iface)
            menu.exec(global_pos)
            return
        menu.addAction(
            f"IPv{family} protocol settings…",
            partial(self._ipgroup_settings_dialog, iface, family),
        )
        menu.addAction(
            f"Add IPv{family} address…", partial(self._add_ip_dialog, iface.name, family)
        )
        gw = iface.gateway_for(family)
        if gw:
            menu.addSeparator()
            menu.addAction(
                f"Clear IPv{family} gateway",
                partial(self._apply, f"Clear IPv{family} gateway on {iface.name}",
                        actions.plan_clear_gateway(iface.name, family),
                        revert=actions.plan_set_gateway(iface.name, gw.address, family)),
            )
        menu.exec(global_pos)

    def _fill_dns_menu(self, menu: QMenu) -> None:
        menu.addAction("Edit manual resolvers…", self._manual_dns_dialog)

    def _add_common_iface_items(self, menu: QMenu, iface: Interface) -> None:
        # A "config" is the whole family: address (static or DHCP/RA), gateway,
        # DNS and search — the rich IpGroup dialog. Adding a bare extra address
        # to a family that already has one lives on the group's region menu.
        menu.addAction(
            "Add IPv4 config…", partial(self._ipgroup_settings_dialog, iface, 4)
        )
        menu.addAction(
            "Add IPv6 config…", partial(self._ipgroup_settings_dialog, iface, 6)
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
                        actions.plan_set_link(iface.name, False),
                        revert=actions.plan_set_link(iface.name, True)),
            )
        else:
            menu.addAction(
                f"Bring {iface.name} up",
                partial(self._apply, f"Bring {iface.name} up",
                        actions.plan_set_link(iface.name, True),
                        revert=actions.plan_set_link(iface.name, False)),
            )

    def _fill_nic_menu(self, menu: QMenu, iface: Interface) -> None:
        self._add_common_iface_items(menu, iface)
        menu.addSeparator()
        if iface.master:
            menu.addAction(
                f"Remove from {iface.master}",
                partial(self._apply, f"Remove {iface.name} from {iface.master}",
                        actions.plan_remove_member(iface.name),
                        revert=actions.plan_add_member(iface.master, iface.name)),
            )
        elif iface.kind == "physical":
            menu.addAction(
                "Create bond with this NIC…",
                partial(self._new_bond_dialog, [iface.name]),
            )
            menu.addAction(
                "Create bridge with this NIC…",
                partial(self._new_bridge_dialog, [iface.name]),
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
                            actions.plan_set_bond_mode(iface.name, value),
                            revert=(actions.plan_set_bond_mode(iface.name, iface.bond_mode)
                                    if iface.bond_mode else None)),
                )
                action.setCheckable(True)
                action.setChecked(value == iface.bond_mode)
        elif iface.kind == "bridge":
            # VLAN filtering toggle
            vlan_label = ("Disable VLAN filtering" if iface.bridge_vlan_aware
                          else "Enable VLAN filtering (vlan-aware)")
            menu.addAction(
                vlan_label,
                partial(self._apply,
                        f"{'Disable' if iface.bridge_vlan_aware else 'Enable'} "
                        f"VLAN filtering on {iface.name}",
                        actions.plan_set_bridge_vlan_aware(iface.name,
                                                            not iface.bridge_vlan_aware),
                        revert=actions.plan_set_bridge_vlan_aware(iface.name,
                                                                   iface.bridge_vlan_aware)),
            )
            # Per-port VLAN membership (only meaningful on vlan-aware bridges)
            if iface.bridge_vlan_aware:
                members = self.state.members_of(iface.name)
                if members:
                    port_menu = menu.addMenu("Configure port VLANs")
                    for member in members:
                        port_menu.addAction(
                            member.name,
                            partial(self._bridge_vlan_port_dialog, member),
                        )
        add_menu = menu.addMenu("Add member")
        free = self.state.free_nics()
        add_menu.setEnabled(bool(free))
        for nic in free:
            add_menu.addAction(
                nic.name,
                partial(self._apply, f"Add {nic.name} to {iface.name}",
                        actions.plan_add_member(iface.name, nic.name),
                        revert=actions.plan_remove_member(nic.name)),
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

    def _fill_draft_vlan_menu(self, menu: QMenu, node: DraftVlanNode) -> None:
        parents = self._vlan_parents()
        create = menu.addMenu("Create on")
        create.setEnabled(bool(parents))
        for iface in parents:
            create.addAction(
                iface.name, partial(self._instantiate_draft_vlan, node, iface.name)
            )
        menu.addAction(
            "Add IPv4 address…", partial(self._add_draft_vlan_address, node, 4)
        )
        menu.addAction(
            "Add IPv6 address…", partial(self._add_draft_vlan_address, node, 6)
        )
        if node.cidrs:
            remove = menu.addMenu("Remove address")
            for cidr in node.cidrs:
                remove.addAction(
                    cidr, partial(self.canvas.remove_draft_vlan_address, node.draft_id, cidr)
                )
        menu.addAction("Edit VLAN…", partial(self._edit_draft_vlan_dialog, node))
        menu.addSeparator()
        menu.addAction(
            "Delete draft", partial(self.canvas.remove_draft_vlan, node.draft_id)
        )

    def _vlan_parents(self) -> list[Interface]:
        """Links a VLAN can be created on: a free physical NIC or a group."""
        if not self.state:
            return []
        return [
            i for i in self.state.interfaces
            if i.master is None and i.kind in ("physical", "bond", "bridge", "team")
        ]

    def _fill_ip_menu(self, menu: QMenu, node: IpNode) -> None:
        if node.is_draft:
            attach = menu.addMenu("Attach to")
            for iface in self._attachable_ifaces():
                attach.addAction(
                    iface.name,
                    partial(self._attach_draft, node, iface.name),
                )
            menu.addAction("Edit config…", partial(self._edit_draft_config_dialog, node))
            menu.addAction("Set name…", partial(self._name_ip_dialog, node))
            menu.addSeparator()
            menu.addAction(
                "Delete draft", partial(self.canvas.remove_draft, node.draft_id)
            )
            return

        if self._docker_owned(node.parent_name):
            # Docker assigns this address (the bridge gateway / a container IP);
            # editing or moving it would break docker, so the box is read-only.
            self._fill_docker_readonly_menu(menu, self.state.get(node.parent_name))
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
        menu.addAction("Delete address", partial(self._delete_ip, node))

    def _attachable_ifaces(self, exclude: str | None = None) -> list[Interface]:
        if not self.state:
            return []
        return [
            i for i in self.state.interfaces
            if i.name != exclude
            and i.master is None
            and (i.kind in ("physical", "vlan", "loopback") or i.kind in GROUP_KINDS)
            and not self.state.is_docker_owned(i)  # can't attach to a docker bridge
        ]

    def _show_canvas_menu(self, global_pos: QPoint, scene_pos: QPointF) -> None:
        menu = QMenu(self)
        menu.addAction(
            "New IPv4 config (draft)…", partial(self._new_draft_dialog, 4, scene_pos)
        )
        menu.addAction(
            "New IPv6 config (draft)…", partial(self._new_draft_dialog, 6, scene_pos)
        )
        menu.addAction(
            "New VLAN (draft)…", partial(self._new_vlan_draft_dialog, scene_pos)
        )
        menu.addSeparator()
        menu.addAction("Create bridge…", self._new_bridge_dialog)
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
                revert=actions.plan_remove_addresses(ifname, [cidr]),
            )

    def _edit_ip_dialog(self, node: IpNode) -> None:
        """Edit one attached address box (a single CIDR). Drafts are whole
        per-family configs and use :meth:`_edit_draft_config_dialog` instead."""
        dialog = IpConfigDialog(self, node.family, initial=node.cidr, name=node.alias)
        if not dialog.exec():
            return
        cidr, name, family, parent = dialog.cidr, dialog.name, node.family, node.parent_name
        rename = lambda: self.canvas.set_ip_name(family, cidr, name)  # noqa: E731
        if cidr != node.cidr:
            plan = actions.plan_remove_addresses(parent, [node.cidr]) + \
                actions.plan_add_addresses(parent, [cidr])
            self._apply(
                f"Edit IPv{family} config on {parent}", plan, on_success=rename,
                revert=(actions.plan_remove_addresses(parent, [cidr])
                        + actions.plan_restore_addresses(parent, [node.cidr])),
            )
        else:
            rename()  # only the name changed; no kernel change needed

    def _add_vlan_dialog(self, ifname: str) -> None:
        dialog = VlanDialog(self, ifname, self.state.link_names() if self.state else set())
        if dialog.exec():
            vlan_name = dialog.name or actions.default_vlan_name(ifname, dialog.vlan_id)
            self._apply(
                f"Create VLAN {dialog.vlan_id} on {ifname}",
                actions.plan_create_vlan(ifname, dialog.vlan_id, dialog.name),
                revert=actions.plan_delete_link(vlan_name),
            )

    def _draft_iface(self, family: int, cidr: str = "", gateway: str = "",
                     dns: list[str] | None = None,
                     dns_search: list[str] | None = None) -> Interface:
        """A throwaway Interface that feeds a draft's staged config into the
        IpGroup dialog (which reads everything off an Interface)."""
        addresses: list[Address] = []
        if cidr:
            try:
                parsed = ipaddress.ip_interface(cidr)
                addresses.append(Address(str(parsed.ip), parsed.network.prefixlen, family))
            except ValueError:
                pass
        gateways = {family: Gateway(gateway)} if gateway else {}
        return Interface(
            name="(draft)", addresses=addresses, gateways=gateways,
            dns=list(dns or []), dns_search=list(dns_search or []),
        )

    def _draft_config_from(self, dlg: IpGroupDialog) -> dict:
        """The parts of an IpGroup dialog result a detached draft can hold:
        a static address, static gateway and static DNS/search (Dynamic means
        "decide on attach", so it stages nothing)."""
        return {
            "cidr": dlg.new_static_address,  # "" when Dynamic
            "gateway": dlg.gateway if dlg.gateway_static else "",
            "dns": dlg.dns_servers if dlg.dns_static else [],
            "dns_search": dlg.dns_search if dlg.dns_static else [],
        }

    def _new_draft_dialog(self, family: int, scene_pos: QPointF) -> None:
        dlg = IpGroupDialog(
            self, self._draft_iface(family), family,
            can_edit_dns=self.state.can_edit_dns if self.state else False,
            title=f"New IPv{family} config (draft)",
        )
        if not dlg.exec():
            return
        cfg = self._draft_config_from(dlg)
        self.canvas.add_draft(family, cfg["cidr"], scene_pos, **{
            k: cfg[k] for k in ("gateway", "dns", "dns_search")
        })

    def _edit_draft_config_dialog(self, node: IpNode) -> None:
        family = node.family
        iface = self._draft_iface(family, node.cidr, node.gateway, node.dns, node.dns_search)
        dlg = IpGroupDialog(
            self, iface, family,
            can_edit_dns=self.state.can_edit_dns if self.state else False,
            title=f"Edit IPv{family} config (draft)",
            initial_static=node.cidr,
        )
        if not dlg.exec():
            return
        cfg = self._draft_config_from(dlg)
        self.canvas.update_draft(node.draft_id, cfg["cidr"], cfg["gateway"],
                                 cfg["dns"], cfg["dns_search"])

    def _new_vlan_draft_dialog(self, scene_pos: QPointF) -> None:
        existing = self.state.link_names() if self.state else set()
        dialog = DraftVlanDialog(self, existing)
        if dialog.exec():
            self.canvas.add_draft_vlan(dialog.vlan_id, dialog.name, scene_pos)

    def _edit_draft_vlan_dialog(self, node: DraftVlanNode) -> None:
        existing = self.state.link_names() if self.state else set()
        dialog = DraftVlanDialog(self, existing, vlan_id=node.vlan_id, name=node.name)
        if dialog.exec():
            self.canvas.update_draft_vlan(node.draft_id, dialog.vlan_id, dialog.name)

    def _add_draft_vlan_address(self, node: DraftVlanNode, family: int) -> None:
        dialog = IpConfigDialog(
            self, family, title=f"Add IPv{family} address to VLAN draft"
        )
        if dialog.exec():
            self.canvas.add_draft_vlan_address(node.draft_id, dialog.cidr)

    def _on_draft_vlan_dropped(self, node: DraftVlanNode, target) -> None:
        self._instantiate_draft_vlan(node, target.iface.name)

    def _instantiate_draft_vlan(self, node: DraftVlanNode, parent_name: str) -> None:
        vlan_id = node.vlan_id
        name = node.name or actions.default_vlan_name(parent_name, vlan_id)
        cidrs = list(node.cidrs)
        draft_id = node.draft_id
        plan = actions.plan_create_vlan(parent_name, vlan_id, name)
        if cidrs:
            plan += actions.plan_add_addresses(name, cidrs)
        self._apply(
            f"Create VLAN {vlan_id} on {parent_name}",
            plan,
            on_success=lambda: self.canvas.remove_draft_vlan(draft_id),
            revert=actions.plan_delete_link(name),
        )

    def _name_ip_dialog(self, node: IpNode) -> None:
        text, ok = QInputDialog.getText(
            self, "Name this address", "Name (blank to clear):", text=node.alias
        )
        if ok:
            self.canvas.set_ip_name(node.family, node.cidr, text.strip())

    def _attach_draft(self, node: IpNode, ifname: str) -> None:
        """Attach a staged draft to ``ifname``: apply its address, gateway and
        DNS together, then drop the draft once they land."""
        iface = self.state.get(ifname) if self.state else None
        if iface is None:
            return
        # A draft is an explicit static config; attaching it does not disturb the
        # target's DHCP lease (address_static stays False), so it stages rather
        # than tears down — matching the existing drag-to-attach behaviour.
        plan, _revert, _changed = self._ipgroup_plan(
            iface, node.family,
            address=node.cidr,
            gateway_static=bool(node.gateway), gateway=node.gateway,
            dns_static=bool(node.dns or node.dns_search),
            dns_servers=node.dns, dns_search=node.dns_search,
        )
        draft_id = node.draft_id
        remove = lambda: self.canvas.remove_draft(draft_id)  # noqa: E731
        if not plan:
            remove()  # nothing to apply (empty draft); just clear it
            return
        self._apply(
            f"Attach IPv{node.family} config to {ifname}", plan, on_success=remove,
        )

    def _clone_ip(self, node: IpNode) -> None:
        self.canvas.add_draft(node.family, node.cidr, node.pos() + QPointF(30, 30))

    def _delete_ip(self, node: IpNode) -> None:
        """Remove one static address. On a host whose backend owns the config
        (NetworkManager et al.) a runtime ``ip addr del`` is reverted within
        seconds, so the delete is deferred to Save (a pending intent, like the
        "→ DHCP" switch); only a runtime-only host deletes immediately."""
        backend = self.state.backend if self.state else None
        if backend is not None and backend.manages_config:
            self._remove_address_intent(node.parent_name, node.cidr, node.family)
        else:
            self._apply(
                f"Delete IPv{node.family} config from {node.parent_name}",
                actions.plan_remove_addresses(node.parent_name, [node.cidr]),
            )

    def _remove_address_intent(self, name: str, cidr: str, family: int) -> None:
        """Record a pending delete of ``cidr`` from ``name`` (managed backends).
        No runtime change: the address stays, flagged for removal, until Save
        rewrites the profile without it. Marks the link unsaved and redraws."""
        self._removed_addresses.add((name, cidr))
        self._unsaved.add(name)
        self._update_save_button()
        if self.state:
            self.state.removed_pending = set(self._removed_addresses)
            self._repopulate()
        self.statusBar().showMessage(f"{cidr} will be removed from {name} when you Save.")

    def _detach_ip(self, node: IpNode) -> None:
        if self._docker_owned(node.parent_name):
            self._refuse_docker(node.parent_name)
            return
        family, cidr, pos = node.family, node.cidr, node.pos()
        self._apply(
            f"Detach IPv{family} config from {node.parent_name}",
            actions.plan_remove_addresses(node.parent_name, [cidr]),
            on_success=lambda: self.canvas.add_draft(family, cidr, pos),
            revert=actions.plan_restore_addresses(node.parent_name, [cidr]),
        )

    def _link_properties_dialog(self, iface: Interface) -> None:
        if not self.state:
            return
        others = self.state.link_names() - {iface.name}
        dlg = LinkPropertiesDialog(self, iface, others)
        if not dlg.exec():
            return
        plan: list[list[str]] = []
        revert: list[list[str]] = []
        changed: list[str] = []
        keys: set[str] = set()  # which link-layer properties changed (for Save)
        # Link-level changes apply under the current name; rename goes last. The
        # revert undoes them in reverse: rename back first (so the property
        # restores below address the link by its original name).
        if dlg.new_name != iface.name:
            revert += actions.plan_rename_link(dlg.new_name, iface.name, iface.is_up)
        if dlg.mtu != iface.mtu:
            plan += actions.plan_set_mtu(iface.name, dlg.mtu)
            revert += actions.plan_set_mtu(iface.name, iface.mtu)
            changed.append("MTU")
            keys.add(persist_link.MTU)
        if dlg.mac != iface.mac:
            plan += actions.plan_set_mac(iface.name, dlg.mac)
            revert += actions.plan_set_mac(iface.name, iface.mac)
            changed.append("MAC")
            keys.add(persist_link.MAC)
        if dlg.link_alias != iface.alias:
            plan += actions.plan_set_alias(iface.name, dlg.link_alias)
            revert += actions.plan_set_alias(iface.name, iface.alias)
            changed.append("alias")
            keys.add(persist_link.ALIAS)
        if dlg.new_name != iface.name:
            plan += actions.plan_rename_link(iface.name, dlg.new_name, iface.is_up)
            changed.append("name")
            keys.add(persist_link.NAME)
        if plan:
            old_name, new_name = iface.name, dlg.new_name
            # Carry forward the boot/original name if this device was already
            # renamed (and not yet Saved) earlier this session.
            boot = self._link_origname.get(old_name, old_name)
            self._apply(
                f"Update {iface.name} ({', '.join(changed)})", plan, revert=revert,
                on_success=lambda: self._record_link_props(old_name, new_name, keys, boot),
            )

    def _ipgroup_plan(self, iface: Interface, family: int, *, address: str = "",
                      address_static: bool = False,
                      gateway_static: bool = False, gateway: str = "",
                      dns_static: bool = False, dns_servers: list[str] | None = None,
                      dns_search: list[str] | None = None,
                      ) -> tuple[list[list[str]], list[list[str]], list[str]]:
        """Build the plan to bring ``iface``'s IPv``family`` config to a desired
        state: a static address, gateway and DNS. Shared by the IPv4/6 settings
        dialog and by attaching a staged draft. Returns ``(plan, revert,
        changed)`` — ``revert`` is the inverse (for Try), ``changed`` names the
        edited parts (for the confirmation title).

        Choosing Static (``address_static``) also removes any DHCP/RA-assigned
        address of this family, so Static *replaces* Dynamic instead of stacking
        a second address on top of the lease. Stopping the DHCP client so it
        can't re-add the lease is the persistence backend's job (roadmap 4a)."""
        dns_servers = dns_servers or []
        dns_search = dns_search or []
        plan: list[list[str]] = []
        revert: list[list[str]] = []
        changed: list[str] = []
        if address and not any(a.cidr == address for a in iface.addresses):
            plan += actions.plan_add_addresses(iface.name, [address])
            revert += actions.plan_remove_addresses(iface.name, [address])
            changed.append("address")
        if address_static:
            for dyn in iface.addresses_for(family):
                # Drop DHCP/RA addresses so Static replaces them — but never the
                # one equal to the chosen static address: deleting that would
                # leave the link with no address at all (the user asked to *keep*
                # it, as static). Making it persistently static is a Save (4a);
                # at runtime there is nothing safe to do, so it is left in place.
                if dyn.dynamic and dyn.cidr != address:
                    plan += actions.plan_remove_addresses(iface.name, [dyn.cidr])
                    # Restore (replace), not add: a DHCP client may have handed
                    # the lease back by the time we revert.
                    revert += actions.plan_restore_addresses(iface.name, [dyn.cidr])
                    if "address" not in changed:
                        changed.append("address")
        # Gateway only when Static is chosen; Dynamic leaves the lease alone.
        if gateway_static:
            current = iface.gateway_for(family)
            current_addr = current.address if current else ""
            if gateway and gateway != current_addr:
                plan += actions.plan_set_gateway(iface.name, gateway, family)
                revert += (actions.plan_set_gateway(iface.name, current_addr, family)
                           if current_addr
                           else actions.plan_clear_gateway(iface.name, family))
                changed.append("gateway")
            elif not gateway and current:
                plan += actions.plan_clear_gateway(iface.name, family)
                revert += actions.plan_set_gateway(iface.name, current.address, family)
                changed.append("gateway")
        # DNS is per-link: keep the other family's servers when setting this one.
        if dns_static:
            other = [s for s in iface.dns if ip_family(s) != family]
            combined = other + dns_servers
            if combined != iface.dns or dns_search != iface.dns_search:
                plan += actions.plan_set_dns(iface.name, combined, dns_search)
                revert += actions.plan_set_dns(iface.name, list(iface.dns),
                                               list(iface.dns_search))
                changed.append("DNS")
        return plan, revert, changed

    def _ipgroup_settings_dialog(self, iface: Interface, family: int) -> None:
        if not self.state:
            return
        ignore_dns = (iface.name, family) in self._dns_off_pending
        dlg = IpGroupDialog(self, iface, family, can_edit_dns=self.state.can_edit_dns,
                            host_dns=self.state.dns, ignore_dhcp_dns=ignore_dns)
        if not dlg.exec():
            return
        # "Ignore DHCP DNS" is a Save-time backend intent (no runtime command).
        # It only applies while the family is on DHCP, so choosing Static clears
        # it; choosing DHCP records whatever the toggle says.
        self._set_dns_off_intent(
            iface.name, family, dlg.dhcp_enabled and not dlg.use_dhcp_dns
        )
        # M5: choosing Dynamic for a family that is currently static is a pending
        # switch to DHCP — recorded and applied at Save (the backend reload does
        # the static→DHCP swap), not a runtime ip command. Choosing Static again
        # cancels a pending switch.
        has_static = any(not a.dynamic and a.scope == "global"
                         for a in iface.addresses_for(family))
        if not dlg.address_static and has_static:
            self._set_dhcp_intent(iface.name, family)
            return
        self._dhcp_pending.discard((iface.name, family))
        plan, revert, changed = self._ipgroup_plan(
            iface, family,
            address=dlg.new_static_address, address_static=dlg.address_static,
            gateway_static=dlg.gateway_static, gateway=dlg.gateway,
            dns_static=dlg.dns_static, dns_servers=dlg.dns_servers, dns_search=dlg.dns_search,
        )
        if plan:
            self._apply(f"Update {iface.name} IPv{family} ({', '.join(changed)})",
                        plan, revert=revert)
        elif dlg.address_static and any(
            a.dynamic and a.cidr == dlg.new_static_address
            for a in iface.addresses_for(family)
        ):
            # Static chosen, but the address is the one DHCP/RA already handed
            # out: there is nothing safe to change at runtime (deleting it would
            # drop the link). Pinning it as static persistently is a Save.
            self.statusBar().showMessage(
                f"{dlg.new_static_address} is already assigned via DHCP/RA — "
                "pin it as static with Save."
            )
        else:
            # No runtime plan and no DHCP switch, but the "ignore DHCP DNS" intent
            # may have changed; redraw so its box marker appears/clears.
            self._repopulate()

    def _manual_dns_dialog(self) -> None:
        if not self.state:
            return
        dlg = ManualDnsDialog(self, self.state.manual_dns)
        if dlg.exec():
            self.canvas.set_manual_dns(dlg.servers)
