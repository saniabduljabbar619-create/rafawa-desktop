# apps/product_management_app/features/profiling/dialogs/advance_settlement_dialog.py

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox,
    QComboBox
)
from PySide2.QtCore import Qt
from PySide2.QtGui import QColor, QBrush

from database.session import get_session
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.services.payment_center import PaymentCenter
from database.models.payment import Payment
from database.services.payment_service import PaymentService

from sqlalchemy import func


class AdvanceSettlementDialog(QDialog):

    def __init__(self, party, user_id, parent=None):
        super().__init__(parent)

        self.party = party
        self.user_id = user_id

        self.setWindowTitle("Advance Settlement Hub")
        self.setMinimumWidth(800) # Slightly widened for the extra column
        self.setMinimumHeight(450)

        layout = QVBoxLayout(self)

        title = QLabel(f"Advance Settlement: {party.full_name}")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        # --------------------------------------------------
        # ADVANCE TABLE (🚧 BRIDGE EXPANSION: 7 Columns now)
        # --------------------------------------------------
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Advance ID",
            "Date",
            "Direction",
            "Ledger Track", # Added to prevent cross-ledger mistakes
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

        # Free-text context for this settlement - same reasoning as the
        # Sale/Purchase payment dialogs: a partial settlement completed
        # weeks or days later should carry a note explaining why (a
        # discount, a special arrangement), not just a machine-generated
        # "Automated pool settlement" string.
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
        self.open_advances_pool = [] # 🚀 Holds open records for the backend engine
        total_advance_in = 0.0
        total_advance_out = 0.0

        with get_session() as s:
            advances = (
                s.query(Advance)
                .filter(Advance.party_id == int(self.party.id))
                .order_by(Advance.created_at.asc()) # Keep chronological order
                .all()
            )

            for adv in advances:
                # Calculate what has already been deducted from this specific record
                settled_alloc = s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0)).filter(
                    AdvanceAllocation.advance_id == adv.id
                ).scalar()

                settled_payments = s.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
                    Payment.entity_type == "advance", Payment.entity_id == adv.id,
                    Payment.status != "voided",
                ).scalar()

                settled = float(settled_alloc or 0) + float(settled_payments or 0)
                remaining = float(adv.amount) - settled

                if remaining <= 0:
                    continue

                # Save active records into an in-memory pool for later distribution
                self.open_advances_pool.append({
                    "id": adv.id,
                    "direction": adv.direction,
                    "remaining": remaining
                })

                if adv.direction == "received":
                    total_advance_in += remaining
                else:
                    total_advance_out += remaining

        # --------------------------------------------------
        # POPULATE A CLEAN CONSOLIDATED TWO-ROW SUMMARY
        # --------------------------------------------------
        self.table.setRowCount(0)
        
        # Row 1: Total Customer Deposits Remaining
        if total_advance_in > 0:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem("POOL-IN"))
            self.table.setItem(r, 1, QTableWidgetItem("All Customer Deposits Combined"))
            self.table.setItem(r, 2, QTableWidgetItem("Advance IN"))
            self.table.setItem(r, 6, QTableWidgetItem(f"{total_advance_in:,.2f}")) # Dynamic balance

        # Row 2: Total Extended Supplier Debts Remaining
        if total_advance_out > 0:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem("POOL-OUT"))
            self.table.setItem(r, 1, QTableWidgetItem("All System Debts Combined"))
            self.table.setItem(r, 2, QTableWidgetItem("Advance OUT"))
            self.table.setItem(r, 6, QTableWidgetItem(f"{total_advance_out:,.2f}"))

    def autofill_amount(self):
        r = self.table.currentRow()
        if r < 0:
            return

        # 🚧 THE BRIDGE: Remaining column shifted from index 5 to index 6
        remaining = self.table.item(r, 6).text()
        remaining = remaining.replace(",", "")
        self.amount_input.setText(remaining)

    # --------------------------------------------------
    # RECORD SETTLEMENT
    # --------------------------------------------------
    def record_payment(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, "Select Pool", "Select an advance pool first.")
            return

        try:
            payment_amount = float(self.amount_input.text())
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Enter a valid amount.")
            return

        pool_type = self.table.item(r, 0).text() # "POOL-IN" or "POOL-OUT"
        target_direction = "received" if pool_type == "POOL-IN" else "given"
        
        # Filter our memory pool to match only the selected direction
        active_records = [a for a in self.open_advances_pool if a["direction"] == target_direction]
        total_pool_available = sum(a["remaining"] for a in active_records)

        if payment_amount > total_pool_available:
            QMessageBox.warning(self, "Over Settlement", f"Amount exceeds total pool balance ({total_pool_available:,.2f}).")
            return

        # Explicitly map cash context directions
        payment_direction = "out" if target_direction == "received" else "in"
        amount_left_to_distribute = payment_amount

        try:
            # 🚀 THE AUTO-KNOCK LOOP
            # This loops through individual records and automatically chips away at balances
            for record in active_records:
                if amount_left_to_distribute <= 0:
                    break

                # Determine how much this specific record can absorb
                absorb_amount = min(amount_left_to_distribute, record["remaining"])

                own_note = self.note_input.text().strip()
                auto_ref = f"Automated pool settlement linked to Advance ID #{record['id']}"
                combined_note = f"{own_note} ({auto_ref})" if own_note else auto_ref

                PaymentService.record_advance_payment(
                    advance_id=record["id"],
                    party_id=int(self.party.id),
                    direction=payment_direction,
                    amount=absorb_amount,
                    created_by_user_id=self.user_id,
                    method=self.method_combo.currentText(),
                    notes=combined_note
                )

                amount_left_to_distribute -= absorb_amount

            QMessageBox.information(self, "Success", f"Successfully deducted ₦{payment_amount:,.2f} from the advance pool.")
            self.load_advances()
            self.amount_input.clear()
            self.note_input.clear()

        except Exception as e:
            QMessageBox.critical(self, "Settlement Failed", str(e))