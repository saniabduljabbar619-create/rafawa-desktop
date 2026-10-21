# apps/product_management_app/features/weigher/dialogs/reassign_dialog.py
# -*- coding: utf-8 -*-
"""
Routing: a weighing was started on the wrong person. Pick who it really belongs to - a registered customer / supplier,
or a walk-in (existing or new) - and the whole weighing (every product, every round) moves to them.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from database.services.weigh_service import WeighService
from shared.auth.permissions import PermissionDenied


class ReassignDialog(QDialog):
    def __init__(self, ticket: dict, parent=None):
        super().__init__(parent)
        self.ticket = ticket
        self.target = None                       # {"id": party id, "label": text}
        self.reason = ""
        self.setWindowTitle("Change customer")
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)

        code = f"  [{ticket['party_code']}]" if ticket.get("party_code") else ""
        head = QLabel(f"<b>{ticket['ticket_no']}</b> is on <b>{ticket['party_name']}</b>{code} - "
                      f"{ticket['total_bags']:g} bags, {ticket['total_kg']:,.2f} kg.<br>"
                      "Everything weighed on it moves to the person you pick.")
        head.setWordWrap(True)
        root.addWidget(head)

        cap = QLabel("MOVE IT TO A CUSTOMER OR SUPPLIER")
        cap.setObjectName("SectionLabel")
        root.addWidget(cap)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a name or phone...")
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)
        self.found = QListWidget()
        self.found.setMinimumHeight(140)
        root.addWidget(self.found)

        row = QHBoxLayout()
        self.btn_walkin = QPushButton("Move to a walk-in…")
        row.addWidget(self.btn_walkin)
        row.addStretch(1)
        root.addLayout(row)

        self.lbl_target = QLabel("Nobody picked yet.")
        self.lbl_target.setStyleSheet("font-weight:700; color:#0f766e;")
        root.addWidget(self.lbl_target)
        self.why = QLineEdit()
        self.why.setPlaceholderText("Why? (optional - e.g. wrong man picked)")
        root.addWidget(self.why)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_move = QPushButton("Move the weighing")
        self.btn_move.setObjectName("PrimaryButton")
        self.btn_move.setEnabled(False)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_move)
        root.addLayout(btns)

        self.search.textChanged.connect(self._load)
        self.found.itemSelectionChanged.connect(self._picked_from_list)
        self.btn_walkin.clicked.connect(self._pick_walkin)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_move.clicked.connect(self._accept)
        self._load()

    def _load(self, *_a):
        self.found.clear()
        try:
            people = WeighService.search_parties(self.search.text(), limit=60)
        except Exception as e:
            QMessageBox.warning(self, "Change customer", str(e))
            people = []
        for p in people:
            if int(p["id"]) == int(self.ticket["party_id"]):
                continue
            it = QListWidgetItem(f"{p['name']}    {p['phone']}    ({p['kind']})".rstrip())
            it.setData(Qt.UserRole, p)
            self.found.addItem(it)

    def _set_target(self, pid: int, label: str):
        self.target = {"id": int(pid), "label": label}
        self.lbl_target.setText(f"Move to:  {label}")
        self.btn_move.setEnabled(True)

    def _picked_from_list(self):
        items = self.found.selectedItems()
        if items:
            p = items[0].data(Qt.UserRole)
            self._set_target(p["id"], p["name"])

    def _pick_walkin(self):
        from apps.product_management_app.features.weigher.dialogs.walkin_pick_dialog import WalkinPickDialog
        dlg = WalkinPickDialog(self)
        if dlg.exec_() == dlg.Accepted and dlg.walkin:
            w = dlg.walkin
            self.found.clearSelection()
            self._set_target(w["id"], f"{w['name']} ({w['code']})" if w.get("code") else w["name"])

    def _accept(self):
        if not self.target:
            return
        self.reason = self.why.text().strip()
        self.accept()


def move_weighing(parent, ticket_id: int) -> bool:
    """Ask who it should move to, confirm, and move it. True when it moved."""
    try:
        t = WeighService.get_ticket(ticket_id)
    except Exception as e:
        QMessageBox.warning(parent, "Change customer", str(e))
        return False
    dlg = ReassignDialog(t, parent)
    if dlg.exec_() != dlg.Accepted or not dlg.target:
        return False
    sure = QMessageBox.question(
        parent, "Change customer",
        f"Move {t['ticket_no']} ({t['total_kg']:,.2f} kg) from {t['party_name']} to {dlg.target['label']}?"
        + ("\n\nIt is already a purchase - the purchase moves with it." if t["status"] == "finalized" else ""),
        QMessageBox.Yes | QMessageBox.No)
    if sure != QMessageBox.Yes:
        return False
    try:
        WeighService.reassign_ticket(ticket_id, new_party_id=dlg.target["id"], reason=dlg.reason)
    except (ValueError, PermissionDenied) as e:
        QMessageBox.warning(parent, "Change customer", str(e))
        return False
    QMessageBox.information(parent, "Change customer", f"Moved to {dlg.target['label']}.")
    return True
