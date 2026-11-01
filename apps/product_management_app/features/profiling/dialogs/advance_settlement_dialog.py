# apps/product_management_app/features/profiling/dialogs/advance_settlement_dialog.py

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox,
    QComboBox
)
from PySide2.QtCore import Qt

from database.session import get_session
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment
from database.services.payment_service import PaymentService

from sqlalchemy import func


class AdvanceSettlementDialog(QDialog):

    def __init__(self, party, user_id, parent=None):
        super().__init__(parent)

        self.party = party
        self.user_id = user_id

        self.setWindowTitle("Advance Settlement Hub")
        self.setMinimumWidth(800)
        self.setMinimumHeight(450)

        layout = QVBoxLayout(self)

        title = QLabel(f"Advance Settlement: {party.full_name}")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        # --------------------------------------------------
        # ADVANCE TABLE (7 Columns)
        # --------------------------------------------------
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Advance ID",
            "Date",
            "Direction",
            "Ledger Track",
            "Amount",
            "Settled",
            "Remaining"
        ])

        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.autofill_amount)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addWidget(self.table)

        # --------------------------------------------------
        # PAYMENT INPUT
        # --------------------------------------------------
        form = QHBoxLayout()

        lbl_amt = QLabel("Payment Amount:")
        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("Enter amount")

        lbl_method = QLabel("Method:")
        self.method_combo = QComboBox()
        self.method_combo.addItems(["cash", "transfer", "bank", "mobile"])

        form.addWidget(lbl_amt)
        form.addWidget(self.amount_input)
        form.addWidget(lbl_method)
        form.addWidget(self.method_combo)

        layout.addLayout(form)

        note_row = QHBoxLayout()
        note_row.addWidget(QLabel("Note:"))
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText(
            "Optional - e.g. \"partial, balance agreed for next week\"")
        note_row.addWidget(self.note_input)
        layout.addLayout(note_row)

        # --------------------------------------------------
        # BUTTONS
        # --------------------------------------------------
        btns = QHBoxLayout()

        self.btn_record = QPushButton("Record External Settlement")
        self.btn_record.setStyleSheet("font-weight: bold; background-color: #2b579a; color: white; padding: 6px;")
        self.btn_close = QPushButton("Close")

        btns.addWidget(self.btn_record)
        btns.addWidget(self.btn_close)

        layout.addLayout(btns)

        self.btn_close.clicked.connect(self.reject)
        self.btn_record.clicked.connect(self.record_payment)

        self.load_advances()

    # --------------------------------------------------
    # LOAD ADVANCES
    # --------------------------------------------------
    def load_advances(self):
        self.open_advances_pool = []
        total_advance_in = 0.0
        total_amount_in = 0.0
        total_settled_in = 0.0
        total_advance_out = 0.0
        total_amount_out = 0.0
        total_settled_out = 0.0

        with get_session() as s:
            advances = (
                s.query(Advance)
                .filter(Advance.party_id == int(self.party.id))
                .filter(Advance.status != "voided")
                .order_by(Advance.created_at.asc())
                .all()
            )

            for adv in advances:
                settled_alloc = s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0)).filter(
                    AdvanceAllocation.advance_id == adv.id
                ).scalar()

                settled_payments = s.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
                    Payment.entity_type == "advance", Payment.entity_id == adv.id,
                    Payment.status != "voided",
                ).scalar()

                amt = float(adv.amount or 0)
                settled = float(settled_alloc or 0) + float(settled_payments or 0)
                remaining = amt - settled

                if remaining <= 0.005:
                    continue

                self.open_advances_pool.append({
                    "id": int(adv.id),
                    "created_at": adv.created_at,
                    "direction": adv.direction,
                    "ledger": adv.ledger or adv.advance_type or "money",
                    "amount": amt,
                    "settled": settled,
                    "remaining": remaining,
                })

                if adv.direction == "received":
                    total_amount_in += amt
                    total_settled_in += settled
                    total_advance_in += remaining
                else:
                    total_amount_out += amt
                    total_settled_out += settled
                    total_advance_out += remaining

        self.table.setRowCount(0)

        for rec in self.open_advances_pool:
            r = self.table.rowCount()
            self.table.insertRow(r)
            cols = [
                f"ADV-{rec['id']}",
                str(rec["created_at"] or "")[:19],
                "Advance IN" if rec["direction"] == "received" else "Advance OUT",
                str(rec["ledger"]),
                f"{rec['amount']:,.2f}",
                f"{rec['settled']:,.2f}",
                f"{rec['remaining']:,.2f}",
            ]
            for c, val in enumerate(cols):
                it = QTableWidgetItem(val)
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)

        if len([a for a in self.open_advances_pool if a["direction"] == "received"]) > 1:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, val in enumerate([
                "POOL-IN", "All Customer Deposits Combined", "Advance IN", "money",
                f"{total_amount_in:,.2f}", f"{total_settled_in:,.2f}", f"{total_advance_in:,.2f}",
            ]):
                it = QTableWidgetItem(val)
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)

        if len([a for a in self.open_advances_pool if a["direction"] == "given"]) > 1:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, val in enumerate([
                "POOL-OUT", "All System Debts Combined", "Advance OUT", "money",
                f"{total_amount_out:,.2f}", f"{total_settled_out:,.2f}", f"{total_advance_out:,.2f}",
            ]):
                it = QTableWidgetItem(val)
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)

    def autofill_amount(self):
        r = self.table.currentRow()
        if r < 0 or self.table.item(r, 6) is None:
            return
        remaining = self.table.item(r, 6).text().replace(",", "")
        self.amount_input.setText(remaining)

    # --------------------------------------------------
    # RECORD SETTLEMENT
    # --------------------------------------------------
    def record_payment(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, "Select Advance", "Select an advance or pool row first.")
            return

        try:
            payment_amount = float(self.amount_input.text().replace(",", ""))
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Enter a valid amount.")
            return

        if payment_amount <= 0:
            QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than zero.")
            return

        row_id_text = self.table.item(r, 0).text().strip()
        if row_id_text.startswith("ADV-"):
            adv_id = int(row_id_text.split("-", 1)[1])
            active_records = [a for a in self.open_advances_pool if a["id"] == adv_id]
            target_direction = active_records[0]["direction"] if active_records else "received"
        else:
            target_direction = "received" if row_id_text == "POOL-IN" else "given"
            active_records = [a for a in self.open_advances_pool if a["direction"] == target_direction]

        total_pool_available = sum(a["remaining"] for a in active_records)

        if payment_amount > total_pool_available + 0.005:
            QMessageBox.warning(
                self, "Over Settlement",
                f"Amount exceeds remaining advance balance ({total_pool_available:,.2f})."
            )
            return

        payment_direction = "out" if target_direction == "received" else "in"
        amount_left_to_distribute = payment_amount

        try:
            for record in active_records:
                if amount_left_to_distribute <= 0:
                    break

                absorb_amount = min(amount_left_to_distribute, record["remaining"])

                own_note = self.note_input.text().strip()
                auto_ref = f"Advance settlement linked to ADV-{record['id']}"
                combined_note = f"{own_note} ({auto_ref})" if own_note else auto_ref

                PaymentService.record_advance_payment(
                    advance_id=record["id"],
                    party_id=int(self.party.id),
                    direction=payment_direction,
                    amount=absorb_amount,
                    created_by_user_id=self.user_id,
                    method=self.method_combo.currentText(),
                    notes=combined_note,
                )

                amount_left_to_distribute -= absorb_amount

            QMessageBox.information(
                self, "Success",
                f"Successfully recorded \u20a6{payment_amount:,.2f} advance payment."
            )
            self.load_advances()
            self.amount_input.clear()
            self.note_input.clear()

        except Exception as e:
            QMessageBox.critical(self, "Settlement Failed", str(e))
