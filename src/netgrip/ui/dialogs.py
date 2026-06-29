"""Dialogs: address/VLAN/bond input and the command confirmation step."""

from __future__ import annotations

import ipaddress
import shlex

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from netgrip.core.actions import (
    BOND_MODES,
    default_vlan_name,
    next_bond_name,
    valid_ipaddr,
    valid_link_name,
    valid_mac,
    write_file_preview,
)
from netgrip.core.model import FirewallState, Interface, NftRule, ip_family
from netgrip.ui import theme

# The choice a user makes in the command-confirmation dialog. Apply changes the
# running config and leaves it; Try does the same but auto-reverts after a
# timeout unless kept (see TryCountdownDialog); cancel does nothing. Save is not
# here — it is a host-wide toolbar action that commits all unsaved changes.
CONFIRM_CANCEL = ""
CONFIRM_APPLY = "apply"
CONFIRM_TRY = "try"


def _error_label() -> QLabel:
    """A red, word-wrapping label for inline validation errors.

    Project rule: a dialog never opens another dialog (no stacked modals), so
    invalid input is reported in-place here rather than via a popup.
    """
    label = QLabel()
    label.setStyleSheet(f"color: {theme.error().name()};")
    label.setWordWrap(True)
    return label


def _hint_label(text: str = "") -> QLabel:
    """A dim, word-wrapping label for inline hints and notes."""
    label = QLabel(text)
    label.setStyleSheet(f"color: {theme.text_dim().name()};")
    label.setWordWrap(True)
    return label


class DynamicStaticField(QWidget):
    """A value with a Dynamic / Static toggle.

    *Dynamic* shows the current (e.g. DHCP-assigned) value, greyed and
    read-only, and means "leave it as it is". *Static* enables the field to
    type a custom value. When ``allow_static`` is false the Static option is
    disabled (e.g. per-link DNS with no systemd-resolved present).

    The Dynamic option can also be hidden at runtime via
    :meth:`set_dynamic_allowed` — used when DHCP is switched off for a whole
    protocol, where every field must be Static and offering "Dynamic" would be
    a lie (there is no lease to defer to).
    """

    def __init__(self, current: str = "", is_dynamic: bool = True,
                 placeholder: str = "", allow_static: bool = True):
        super().__init__()
        self._current = current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._radio_row = QWidget()
        radios = QHBoxLayout(self._radio_row)
        radios.setContentsMargins(0, 0, 0, 0)
        self._dynamic_btn = QRadioButton("Dynamic")
        self._static_btn = QRadioButton("Static")
        self._static_btn.setEnabled(allow_static)
        group = QButtonGroup(self)
        group.addButton(self._dynamic_btn)
        group.addButton(self._static_btn)
        radios.addWidget(self._dynamic_btn)
        radios.addWidget(self._static_btn)
        radios.addStretch(1)
        layout.addWidget(self._radio_row)

        self._edit = QLineEdit(current)
        self._edit.setPlaceholderText(placeholder)
        layout.addWidget(self._edit)

        self._dynamic_btn.toggled.connect(self._sync)
        start_static = bool(allow_static and not is_dynamic)
        (self._static_btn if start_static else self._dynamic_btn).setChecked(True)
        self._sync()

    def _sync(self) -> None:
        dynamic = self._dynamic_btn.isChecked()
        self._edit.setReadOnly(dynamic)
        # Qt greys a read-only field weakly; disabling reads as clearly inert.
        self._edit.setEnabled(not dynamic)
        if dynamic:
            self._edit.setText(self._current)

    def set_dynamic_allowed(self, allowed: bool) -> None:
        """Show or hide the Dynamic option. When disallowed, the row of radios
        is hidden and the field locks to Static (a plain editable value) — there
        is nothing dynamic to defer to (DHCP is off for the protocol)."""
        self._radio_row.setVisible(allowed)
        if not allowed:
            self._static_btn.setChecked(True)
        self._sync()

    @property
    def is_static(self) -> bool:
        return self._static_btn.isChecked()

    def value(self) -> str:
        return self._edit.text().strip()


def parse_cidrs(text: str, family: int) -> list[str]:
    """Validate and normalize one-CIDR-per-line input. Raises ValueError."""
    cidrs: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = ipaddress.ip_interface(line)
        except ValueError as exc:
            raise ValueError(f"'{line}' is not a valid address: {exc}") from exc
        if parsed.version != family:
            raise ValueError(f"'{line}' is not an IPv{family} address.")
        normalized = parsed.with_prefixlen
        if normalized not in cidrs:
            cidrs.append(normalized)
    if not cidrs:
        raise ValueError("Enter at least one address.")
    return cidrs


class IpConfigDialog(QDialog):
    """Edit one IP address (a single CIDR) and an optional free-form name."""

    def __init__(self, parent, family: int, initial: str = "", name: str = "",
                 title: str | None = None):
        super().__init__(parent)
        self.family = family
        self.cidr = ""
        self.name = ""
        self.setWindowTitle(title or f"IPv{family} configuration")

        example = "192.168.1.20/24" if family == 4 else "2001:db8::20/64"
        form = QFormLayout(self)
        self._addr_edit = QLineEdit(initial)
        self._addr_edit.setPlaceholderText(example)
        self._addr_edit.setMinimumWidth(300)
        form.addRow("Address (CIDR):", self._addr_edit)
        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText("optional label, e.g. uplink")
        form.addRow("Name:", self._name_edit)
        self._error = _error_label()
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        try:
            # Reuse the list validator, then take the single address from it.
            self.cidr = parse_cidrs(self._addr_edit.text(), self.family)[0]
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        self.name = self._name_edit.text().strip()
        self.accept()


class LinkPropertiesDialog(QDialog):
    """Edit a link's name, MAC, MTU and alias.

    Gateway and DNS are per-family — a DHCP/RA lease hands them out per
    protocol — so they live on the IPv4/IPv6 groups (see IpGroupDialog), not on
    the link as a whole.
    """

    def __init__(self, parent, iface: Interface, other_names: set[str]):
        super().__init__(parent)
        self.setWindowTitle(f"{iface.name} properties")
        self._iface = iface
        self._others = other_names
        # Results read by the caller after exec():
        self.new_name = iface.name
        self.mac = iface.mac
        self.mtu = iface.mtu
        self.link_alias = iface.alias

        form = QFormLayout(self)
        self._name_edit = QLineEdit(iface.name)
        form.addRow("Name:", self._name_edit)
        self._mac_edit = QLineEdit(iface.mac)
        self._mac_edit.setPlaceholderText("xx:xx:xx:xx:xx:xx")
        form.addRow("MAC address:", self._mac_edit)
        self._mtu_spin = QSpinBox()
        self._mtu_spin.setRange(68, 65536)
        self._mtu_spin.setValue(iface.mtu or 1500)
        form.addRow("MTU:", self._mtu_spin)
        self._alias_edit = QLineEdit(iface.alias)
        self._alias_edit.setPlaceholderText("optional label stored on the link")
        form.addRow("Alias:", self._alias_edit)

        self._error = _error_label()
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        name = self._name_edit.text().strip()
        if not valid_link_name(name):
            self._error.setText(
                "Interface names are 1-15 characters: letters, digits, '.', '-', '_'."
            )
            return
        if name != self._iface.name and name in self._others:
            self._error.setText(f"'{name}' already exists.")
            return
        mac = self._mac_edit.text().strip()
        if mac and not valid_mac(mac):
            self._error.setText("Enter a unicast MAC like 52:54:00:a1:b2:c3.")
            return
        self.new_name = name
        self.mac = mac or self._iface.mac  # blank means leave it unchanged
        self.mtu = self._mtu_spin.value()
        self.link_alias = self._alias_edit.text().strip()
        self.accept()


class IpGroupDialog(QDialog):
    """Per-family *protocol settings* for one interface: whether the family is
    driven by DHCP/RA or configured statically, plus the address and default
    gateway it carries.

    A single **DHCP enabled / disabled** toggle leads. With DHCP enabled the
    address and gateway default to the lease but each can be pinned Static
    (e.g. DHCP address, static gateway); with it disabled every field is Static
    and the Dynamic option is hidden, since there is no lease to defer to.

    DNS is deliberately *not* edited here. Resolvers apply host-wide (they live
    in resolv.conf, not per-protocol), so they are owned by the System DNS box;
    this dialog only points there.

    The same dialog backs three flows: per-family *settings* on an existing
    interface, *adding* a family's config to one that has none yet, and
    composing a detached *draft* config. For a draft, pass ``initial_static``
    (the draft's own static address, so it pre-fills) and a ``title``.
    """

    def __init__(self, parent, iface: Interface, family: int, can_edit_dns: bool = False,
                 *, title: str | None = None, initial_static: str = "",
                 host_dns: list[str] | None = None, ignore_dhcp_dns: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title or f"{iface.name} · IPv{family} protocol settings")
        self._iface = iface
        self._family = family
        gw = iface.gateway_for(family)
        dyn_addr = next((a for a in iface.addresses_for(family) if a.dynamic), None)
        # The link's existing static address for this family (first global,
        # non-dynamic one). Pre-filling Static with it means an interface that is
        # already statically configured opens *showing* that address as Static,
        # rather than silently defaulting to an empty Dynamic field — which hid
        # the current config and (with the 0.2 Dynamic=teardown) would risk
        # wiping it on a no-touch OK. A DHCP/RA lease still wins (Dynamic).
        static_addr = next(
            (a for a in iface.addresses_for(family) if not a.dynamic and a.scope == "global"),
            None,
        )
        prefill_static = initial_static or (static_addr.cidr if static_addr else "")
        # Results read by the caller after exec():
        self.address_static = False
        self.new_static_address = ""
        self.gateway_static = False
        self.gateway = gw.address if gw else ""
        # DNS is not edited here (host-wide, see class docstring). Kept as empty
        # results so the shared apply/draft code paths stay untouched.
        self.dns_static = False
        self.dns_servers: list[str] = []
        self.dns_search: list[str] = []

        form = QFormLayout(self)
        # The lead control: is this protocol driven by DHCP/RA, or static? A
        # family already holding a lease opens DHCP-enabled; a static one (or a
        # family with no address yet, where Dynamic would be a runtime no-op
        # until the 0.2 backend starts a client) opens DHCP-disabled.
        self._dhcp_on = QRadioButton("DHCP enabled (dynamic)")
        self._dhcp_off = QRadioButton("DHCP disabled (static)")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._dhcp_on)
        mode_group.addButton(self._dhcp_off)
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(self._dhcp_on)
        mode_layout.addWidget(self._dhcp_off)
        mode_layout.addStretch(1)
        form.addRow("Protocol:", mode_row)
        self._mode_hint = _hint_label()
        form.addRow("", self._mode_hint)

        self._addr_field = DynamicStaticField(
            current=dyn_addr.cidr if dyn_addr else prefill_static,
            is_dynamic=bool(dyn_addr),
            placeholder="e.g. 192.168.1.20/24" if family == 4 else "e.g. 2001:db8::20/64",
        )
        form.addRow("Address:", self._addr_field)
        self._gw_field = DynamicStaticField(
            current=gw.address if gw else "",
            is_dynamic=(gw.dynamic if gw else True),
            placeholder="e.g. 192.168.1.1" if family == 4 else "e.g. 2001:db8::1",
        )
        form.addRow("Default gateway:", self._gw_field)

        # No DNS *field* — resolvers can't be typed per protocol. There is a
        # single toggle for whether to accept the lease's DNS (a Save-time
        # backend setting; no runtime command), shown only under DHCP. Below it,
        # a read-only note shows the DNS that is actually attributable to this
        # link: per-link servers (systemd-resolved) or the host-wide ones
        # inferred from its DHCP lease; otherwise it points at the System DNS box.
        self._per_link = iface.dns_for(family)
        self._dhcp_dns = iface.dhcp_dns_for(family, host_dns or [])
        self._use_dhcp_dns = QCheckBox("Use DNS provided by DHCP")
        self._use_dhcp_dns.setChecked(not ignore_dhcp_dns)
        self._use_dhcp_dns.toggled.connect(self._update_dns_note)
        form.addRow("DNS:", self._use_dhcp_dns)
        self._dns_note = _hint_label()
        form.addRow("", self._dns_note)

        self._dhcp_on.toggled.connect(self._sync_mode)
        self._dhcp_on.setChecked(bool(dyn_addr))
        self._dhcp_off.setChecked(not dyn_addr)
        self._sync_mode()

        self._error = _error_label()
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _sync_mode(self) -> None:
        """Reflect the DHCP toggle: when off, every field is Static (the Dynamic
        option is hidden) and there is no lease DNS to accept; when on, fields may
        be pinned Static individually and the 'use DHCP DNS' toggle applies."""
        dhcp = self._dhcp_on.isChecked()
        self._addr_field.set_dynamic_allowed(dhcp)
        self._gw_field.set_dynamic_allowed(dhcp)
        self._use_dhcp_dns.setVisible(dhcp)
        if dhcp:
            self._mode_hint.setText(
                "Address and gateway are assigned by DHCP/RA. Pin a field to "
                "Static to override just that one."
            )
        else:
            self._mode_hint.setText(
                "Set the address and gateway by hand. Nothing on this protocol is "
                "auto-assigned."
            )
        self._update_dns_note()

    def _update_dns_note(self) -> None:
        """The read-only DNS line under the toggle, for the current mode."""
        if not self._dhcp_on.isChecked():
            self._dns_note.setText(
                "Managed host-wide — set resolvers in the System DNS box."
            )
        elif not self._use_dhcp_dns.isChecked():
            self._dns_note.setText(
                "The lease's DNS will be ignored — set resolvers in the System "
                "DNS box."
            )
        elif self._per_link:
            self._dns_note.setText(
                "From this link: " + " ".join(self._per_link)
                + ("  (DHCP)" if self._iface.dns_dynamic else "")
            )
        elif self._dhcp_dns:
            self._dns_note.setText("From DHCP: " + " ".join(self._dhcp_dns))
        else:
            self._dns_note.setText("Provided by DHCP.")

    @property
    def dhcp_enabled(self) -> bool:
        return self._dhcp_on.isChecked()

    @property
    def use_dhcp_dns(self) -> bool:
        return self._use_dhcp_dns.isChecked()

    def _accept(self) -> None:
        dhcp = self._dhcp_on.isChecked()
        # DHCP off forces Static everywhere; DHCP on honours each field's toggle.
        address_static = (not dhcp) or self._addr_field.is_static
        gateway_static = (not dhcp) or self._gw_field.is_static

        new_static = ""
        addr = self._addr_field.value()
        if address_static and addr:
            try:
                parsed = ipaddress.ip_interface(addr)
            except ValueError:
                self._error.setText(f"'{addr}' is not a valid address.")
                return
            if parsed.version != self._family:
                self._error.setText(f"Enter an IPv{self._family} address (with prefix).")
                return
            new_static = parsed.with_prefixlen
        gw = self._gw_field.value()
        if gateway_static and gw:
            if not valid_ipaddr(gw):
                self._error.setText(f"'{gw}' is not a valid gateway address.")
                return
            if ip_family(gw) != self._family:
                self._error.setText(f"The gateway must be an IPv{self._family} address.")
                return
        self.address_static = address_static
        self.new_static_address = new_static
        self.gateway_static = gateway_static
        self.gateway = gw if gateway_static else ""
        self.accept()


class ManualDnsDialog(QDialog):
    """Edit the host-wide manual resolvers recorded for the System DNS box."""

    def __init__(self, parent, servers: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Manual DNS resolvers")
        self.servers = list(servers)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("One resolver per line (host-wide extras):"))
        self._edit = QPlainTextEdit("\n".join(servers))
        self._edit.setMinimumWidth(320)
        layout.addWidget(self._edit)
        note = QLabel(
            "Recorded here and shown with their provenance. Applying host-wide "
            "DNS persistently is the 0.2 backend; per-link DNS is set from each "
            "IPv4/IPv6 group."
        )
        note.setStyleSheet("color: #777;")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._error = _error_label()
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        out: list[str] = []
        for raw in self._edit.toPlainText().splitlines():
            server = raw.strip()
            if not server:
                continue
            if not valid_ipaddr(server):
                self._error.setText(f"'{server}' is not a valid IP address.")
                return
            if server not in out:
                out.append(server)
        self.servers = out
        self.accept()


class VlanDialog(QDialog):
    def __init__(self, parent, parent_ifname: str, existing_names: set[str]):
        super().__init__(parent)
        self.setWindowTitle(f"New VLAN on {parent_ifname}")
        self._parent_ifname = parent_ifname
        self._existing = existing_names
        self.vlan_id = 1
        self.name = ""

        form = QFormLayout(self)
        self._id_spin = QSpinBox()
        self._id_spin.setRange(1, 4094)
        form.addRow("VLAN id:", self._id_spin)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(default_vlan_name(parent_ifname, 1))
        form.addRow("Interface name:", self._name_edit)
        self._id_spin.valueChanged.connect(
            lambda v: self._name_edit.setPlaceholderText(default_vlan_name(parent_ifname, v))
        )
        self._error = _error_label()
        form.addRow(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        self.vlan_id = self._id_spin.value()
        self.name = self._name_edit.text().strip() or default_vlan_name(
            self._parent_ifname, self.vlan_id
        )
        if not valid_link_name(self.name):
            self._error.setText(
                "Interface names are 1-15 characters: letters, digits, '.', '-', '_'."
            )
            return
        if self.name in self._existing:
            self._error.setText(f"'{self.name}' already exists.")
            return
        self.accept()


class DraftVlanDialog(QDialog):
    """Id and optional name for a VLAN draft. The parent is not chosen here — it
    is picked when the draft is dragged onto (or created on) a link."""

    def __init__(self, parent, existing_names: set[str], vlan_id: int = 1, name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("VLAN draft")
        self._existing = existing_names
        self.vlan_id = vlan_id
        self.name = name

        form = QFormLayout(self)
        self._id_spin = QSpinBox()
        self._id_spin.setRange(1, 4094)
        self._id_spin.setValue(vlan_id)
        form.addRow("VLAN id:", self._id_spin)
        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText("optional — named after the parent on connect")
        form.addRow("Interface name:", self._name_edit)
        self._error = _error_label()
        form.addRow(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        self.vlan_id = self._id_spin.value()
        name = self._name_edit.text().strip()
        if name and not valid_link_name(name):
            self._error.setText(
                "Interface names are 1-15 characters: letters, digits, '.', '-', '_'."
            )
            return
        if name and name in self._existing:
            self._error.setText(f"'{name}' already exists.")
            return
        self.name = name
        self.accept()


class BondDialog(QDialog):
    """Name, mode and member selection for a new bond."""

    def __init__(self, parent, free_nics: list[str], preselected: list[str],
                 existing_names: set[str]):
        super().__init__(parent)
        self.setWindowTitle("New bond")
        self._existing = existing_names
        self.name = ""
        self.mode = "active-backup"
        self.members: list[str] = []

        form = QFormLayout(self)
        self._name_edit = QLineEdit(next_bond_name(existing_names))
        form.addRow("Bond name:", self._name_edit)

        self._mode_combo = QComboBox()
        for value, label in BOND_MODES.items():
            self._mode_combo.addItem(label, value)
        form.addRow("Mode:", self._mode_combo)

        self._member_list = QListWidget()
        for nic in free_nics:
            item = QListWidgetItem(nic)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if nic in preselected else Qt.CheckState.Unchecked
            )
            self._member_list.addItem(item)
        form.addRow("Members:", self._member_list)
        self._error = _error_label()
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        self.name = self._name_edit.text().strip()
        if not valid_link_name(self.name):
            self._error.setText(
                "Interface names are 1-15 characters: letters, digits, '.', '-', '_'."
            )
            return
        if self.name in self._existing:
            self._error.setText(f"'{self.name}' already exists.")
            return
        self.mode = self._mode_combo.currentData()
        self.members = [
            self._member_list.item(i).text()
            for i in range(self._member_list.count())
            if self._member_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not self.members:
            self._error.setText("Select at least one member NIC.")
            return
        self.accept()


def render_plan(commands: list[list[str]]) -> str:
    """Format a plan for the confirmation view. A file-write step (see
    :func:`plan_write_file`) is shown as ``# write <path>:`` followed by its
    indented body, so Save is reviewable as a file rather than an opaque quoted
    heredoc; every other command renders as its plain ``shlex``-quoted argv."""
    lines: list[str] = []
    for argv in commands:
        preview = write_file_preview(argv)
        if preview:
            path, body = preview
            lines.append(f"# write {path}:")
            lines.extend(f"    {line}" for line in body.splitlines())
        else:
            lines.append(shlex.join(argv))
    return "\n".join(lines)


def confirm_commands(parent, title: str, commands: list[list[str]], host_label: str,
                     *, allow_try: bool = False, try_seconds: int = 60) -> str:
    """Show the exact commands a plan will run and ask how to apply it.

    Returns one of ``CONFIRM_APPLY`` / ``CONFIRM_TRY`` / ``CONFIRM_CANCEL``.
    *Apply* runs the plan and leaves it; *Try* (offered only when ``allow_try``,
    i.e. the gesture has a safe inverse) runs it but auto-reverts after
    ``try_seconds`` unless kept. Neither persists across reboots — that is the
    toolbar's Save."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    choice = CONFIRM_CANCEL
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"netgrip will run this on <b>{host_label}</b> (as root):"))

    text = QPlainTextEdit(render_plan(commands))
    text.setReadOnly(True)
    mono = QFont("Monospace")
    mono.setStyleHint(QFont.StyleHint.TypeWriter)
    text.setFont(mono)
    text.setMinimumWidth(460)
    layout.addWidget(text)

    note = QLabel(
        (f"<b>Try</b> applies this now and automatically reverts after "
         f"{try_seconds}s unless you keep it — a safe way to test a change that "
         f"could drop your connection.<br><b>Apply</b> applies it and leaves it. "
         if allow_try else "")
        + "Changes affect the running system only; use <b>Save</b> (toolbar) to "
        "persist them across reboots."
    )
    note.setStyleSheet("color: #777;")
    note.setWordWrap(True)
    layout.addWidget(note)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel_btn = QPushButton("Cancel")
    cancel_btn.clicked.connect(dialog.reject)
    buttons.addWidget(cancel_btn)

    def pick(value: str) -> None:
        nonlocal choice
        choice = value
        dialog.accept()

    if allow_try:
        try_btn = QPushButton(f"Try ({try_seconds}s)")
        try_btn.clicked.connect(lambda: pick(CONFIRM_TRY))
        buttons.addWidget(try_btn)
    apply_btn = QPushButton("Apply")
    apply_btn.setDefault(True)
    apply_btn.clicked.connect(lambda: pick(CONFIRM_APPLY))
    buttons.addWidget(apply_btn)
    layout.addLayout(buttons)

    dialog.exec()
    return choice


class TryCountdownDialog(QDialog):
    """Asks whether to keep a *tried* change before its automatic revert fires.

    The change is already on the running config; this counts down and offers
    **Keep** (cancel the revert) or **Revert now**. Timing out — or closing the
    dialog — means revert, the safe default. The matching host-side timer (armed
    a little longer, see actions.plan_try) is the backup if this client dies."""

    def __init__(self, parent, title: str, seconds: int):
        super().__init__(parent)
        self.setWindowTitle(f"Trying: {title}")
        self.kept = False
        self._remaining = seconds

        layout = QVBoxLayout(self)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setMinimumWidth(380)
        layout.addWidget(self._label)

        row = QHBoxLayout()
        row.addStretch(1)
        revert_btn = QPushButton("Revert now")
        revert_btn.clicked.connect(self._revert)
        row.addWidget(revert_btn)
        keep_btn = QPushButton("Keep")
        keep_btn.setDefault(True)
        keep_btn.clicked.connect(self._keep)
        row.addWidget(keep_btn)
        layout.addLayout(row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._render()

    def _render(self) -> None:
        self._label.setText(
            f"Applied to the running config. Reverting automatically in "
            f"<b>{self._remaining}s</b> unless you keep it."
        )

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._finish(kept=False)
        else:
            self._render()

    def _keep(self) -> None:
        self._finish(kept=True)

    def _revert(self) -> None:
        self._finish(kept=False)

    def _finish(self, *, kept: bool) -> None:
        self._timer.stop()
        self.kept = kept
        self.accept()


class FirewallDialog(QDialog):
    """Read-only view of nftables rules that reference one interface.

    Groups rules by table → chain, shows the handle, expression summary, and
    optional comment.  Offers plan-gated add/delete via the caller's ``apply``
    callback so no action happens without user confirmation.
    """

    def __init__(
        self,
        iface_name: str,
        firewall: FirewallState,
        apply_cb,   # callable(title, plan) — same as MainWindow._apply
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Firewall — {iface_name}")
        self.resize(640, 400)
        self._iface = iface_name
        self._firewall = firewall
        self._apply_cb = apply_cb

        root = QVBoxLayout(self)

        if not firewall.available:
            root.addWidget(_hint_label("nftables not available on this host (nft not found)."))
            root.addWidget(QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self))
            self.findChild(QDialogButtonBox).rejected.connect(self.reject)
            return

        rules = firewall.rules_for_iface(iface_name)
        if not rules:
            root.addWidget(_hint_label(
                f"No nftables rules reference {iface_name}.\n"
                "Use 'Add rule…' to create one, or rules may reference this interface "
                "by a wildcard/prefix match (not shown here)."
            ))
        else:
            self._list = QListWidget()
            self._list.setFont(QFont("Monospace"))
            self._populate(rules)
            root.addWidget(self._list)

        btn_bar = QHBoxLayout()
        add_btn = QPushButton("Add rule…")
        add_btn.clicked.connect(self._add_rule)
        btn_bar.addWidget(add_btn)
        btn_bar.addStretch()
        if rules:
            del_btn = QPushButton("Delete selected rule")
            del_btn.clicked.connect(self._delete_rule)
            btn_bar.addWidget(del_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)
        root.addLayout(btn_bar)

    def _populate(self, rules: list[NftRule]) -> None:
        for rule in rules:
            prefix = f"[{rule.family}/{rule.table}/{rule.chain} h={rule.handle}]"
            label = f"{prefix}  {rule.expr_summary}"
            if rule.comment:
                label += f"  # {rule.comment}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self._list.addItem(item)

    def _selected_rule(self) -> NftRule | None:
        if not hasattr(self, "_list"):
            return None
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add_rule(self) -> None:
        from netgrip.core import actions as _actions

        chains_for_iface = self._firewall.chains_for_iface(self._iface)
        if not chains_for_iface:
            _hint_label("No chains reference this interface; create a chain first with nft.")
            return

        # Simple two-field dialog: pick chain, enter expression
        dlg = QDialog(self)
        dlg.setWindowTitle("Add nft rule")
        vbox = QVBoxLayout(dlg)
        form = QFormLayout()

        chain_combo = QComboBox()
        for table, chain in chains_for_iface:
            chain_combo.addItem(
                f"{chain.family}/{table.name}/{chain.name}",
                (chain.family, table.name, chain.name),
            )
        form.addRow("Chain:", chain_combo)

        expr_edit = QLineEdit()
        expr_edit.setPlaceholderText(f"e.g. iifname {self._iface} tcp dport 443 accept")
        form.addRow("Expression:", expr_edit)

        err = _error_label()
        vbox.addLayout(form)
        vbox.addWidget(err)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vbox.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        expr = expr_edit.text().strip()
        if not expr:
            return
        family, table, chain = chain_combo.currentData()
        plan = _actions.plan_nft_add_rule(family, table, chain, expr)
        self._apply_cb(f"Add nft rule to {chain}", plan)
        self.accept()

    def _delete_rule(self) -> None:
        from netgrip.core import actions as _actions

        rule = self._selected_rule()
        if rule is None:
            return
        plan = _actions.plan_nft_delete_rule(rule.family, rule.table, rule.chain, rule.handle)
        summary = rule.expr_summary or f"handle {rule.handle}"
        self._apply_cb(f"Delete nft rule: {summary}", plan)
        self.accept()
