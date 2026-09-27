# apps/product_management_app/features/admin_users/views/user_activity_details_dialog.py

from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QMessageBox
)
from PySide2.QtCore import Qt

from database.services.user_activity_details_service import UserActivityDetailsService


class UserActivityDetailsDialog(QDialog):
    def __init__(self, parent=None, *, actor_user_id: int, actor_role: str, kind: str, entity_id: int):
        super().__init__(parent)

        self.actor_user_id = int(actor_user_id or 0)
        self.actor_role = (actor_role or "").strip().lower()
        self.kind = (kind or "").strip().lower()
        self.entity_id = int(entity_id or 0)

        self.setWindowTitle("Activity Details")
        self.setModal(True)
        self.resize(760, 560)

        # ✅ ROOT LAYOUT MUST EXIST
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        try:
            data = UserActivityDetailsService.get_details(
                actor_user_id=self.actor_user_id,
                actor_role=self.actor_role,
                kind=self.kind,
                entity_id=self.entity_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Details", str(e))
            self.reject()
            return

        # --------------------
        # Header
        # --------------------
        title = QLabel(str(data.get("title") or "Details"))
        title.setStyleSheet("font-size: 16px; font-weight: 900;")
        root.addWidget(title)

        meta = QLabel(str(data.get("subtitle") or ""))
        meta.setStyleSheet("color:#555;")
        root.addWidget(meta)

        # --------------------
        # Party
        # --------------------
        party = data.get("party") or {}

        pb = QGroupBox("Party")
        pb.setStyleSheet("QGroupBox { font-weight: 800; }")
        pl = QVBoxLayout(pb)
        pl.addWidget(QLabel(f"Name: {party.get('name','-')}"))
        pl.addWidget(QLabel(f"Phone: {party.get('phone','-')}"))
        root.addWidget(pb)

        # --------------------
        # Summary
        # --------------------
        summ = data.get("summary") or {}

        sb = QGroupBox("Summary")
        sb.setStyleSheet("QGroupBox { font-weight: 800; }")
        sl = QVBoxLayout(sb)
        sl.addWidget(QLabel(f"Type: {summ.get('type','-')}"))
        sl.addWidget(QLabel(f"Status: {summ.get('status','-')}"))
        sl.addWidget(QLabel(f"Ledger: {summ.get('ledger','-')}"))
        sl.addWidget(QLabel(f"Amount: {summ.get('amount','-')}"))

        if summ.get("method"):
            sl.addWidget(QLabel(f"Method: {summ.get('method','-')}"))
        if summ.get("notes"):
            sl.addWidget(QLabel(f"Notes: {summ.get('notes','-')}"))

        root.addWidget(sb)

        # --------------------
        # Items (for purchase/sale)
        # --------------------
        items = data.get("items") or []

        if items:
            t = QTableWidget(0, 6)
            t.setHorizontalHeaderLabels(
                ["Product", "Type", "Bags", "KG", "Rate", "Line Total"]
            )
            t.verticalHeader().setVisible(False)
            t.setAlternatingRowColors(True)
            t.setEditTriggers(t.NoEditTriggers)
            t.setSelectionBehavior(t.SelectRows)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

            t.setRowCount(len(items))

            for r, it in enumerate(items):
                t.setItem(r, 0, QTableWidgetItem(str(it.get("product") or "")))
                t.setItem(r, 1, QTableWidgetItem(str(it.get("product_type") or "")))
                t.setItem(r, 2, QTableWidgetItem(str(it.get("bags") or 0)))
                t.setItem(r, 3, QTableWidgetItem(str(it.get("kg") or 0)))
                t.setItem(r, 4, QTableWidgetItem(str(it.get("rate") or 0)))
                t.setItem(r, 5, QTableWidgetItem(str(it.get("line_total") or 0)))

                for c in (2, 3, 4, 5):
                    t.item(r, c).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            root.addWidget(t, 1)
        else:
            root.addStretch(1)