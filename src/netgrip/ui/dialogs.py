"""Dialogs: address/VLAN/bond input and the command confirmation step."""

from __future__ import annotations

import ipaddress
import shlex

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from netgrip.core.actions import (
    BOND_MODES,
    default_vlan_name,
    next_bond_name,
    valid_link_name,
)


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
    """Edit the address list of one IP box (one address per line, CIDR)."""

    def __init__(self, parent, family: int, initial: list[str] | None = None,
                 title: str | None = None):
        super().__init__(parent)
        self.family = family
        self.cidrs: list[str] = []
        self.setWindowTitle(title or f"IPv{family} configuration")

        example = "192.168.1.20/24" if family == 4 else "2001:db8::20/64"
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"One address per line, CIDR notation (e.g. {example}):"))
        self._edit = QPlainTextEdit("\n".join(initial or []))
        self._edit.setMinimumWidth(320)
        layout.addWidget(self._edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            self.cidrs = parse_cidrs(self._edit.toPlainText(), self.family)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid address", str(exc))
            return
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
            QMessageBox.warning(
                self, "Invalid name",
                "Interface names are 1-15 characters: letters, digits, '.', '-', '_'.",
            )
            return
        if self.name in self._existing:
            QMessageBox.warning(self, "Name in use", f"'{self.name}' already exists.")
            return
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        self.name = self._name_edit.text().strip()
        if not valid_link_name(self.name):
            QMessageBox.warning(
                self, "Invalid name",
                "Interface names are 1-15 characters: letters, digits, '.', '-', '_'.",
            )
            return
        if self.name in self._existing:
            QMessageBox.warning(self, "Name in use", f"'{self.name}' already exists.")
            return
        self.mode = self._mode_combo.currentData()
        self.members = [
            self._member_list.item(i).text()
            for i in range(self._member_list.count())
            if self._member_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not self.members:
            QMessageBox.warning(self, "No members", "Select at least one member NIC.")
            return
        self.accept()


def confirm_commands(parent, title: str, commands: list[list[str]], host_label: str) -> bool:
    """Show the exact commands a plan will run and ask for confirmation."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"netgrip will run this on <b>{host_label}</b> (as root):"))

    text = QPlainTextEdit("\n".join(shlex.join(argv) for argv in commands))
    text.setReadOnly(True)
    mono = QFont("Monospace")
    mono.setStyleHint(QFont.StyleHint.TypeWriter)
    text.setFont(mono)
    text.setMinimumWidth(460)
    layout.addWidget(text)

    note = QLabel(
        "Changes apply to the running system only and are not persisted "
        "across reboots (see the roadmap)."
    )
    note.setStyleSheet("color: #777;")
    note.setWordWrap(True)
    layout.addWidget(note)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted
