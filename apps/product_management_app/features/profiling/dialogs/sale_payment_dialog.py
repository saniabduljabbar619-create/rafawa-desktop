# apps\product_management_app\features\profiling\dialogs\sale_payment_dialog.py

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox,
    QRadioButton, QButtonGroup
)

from PySide2.QtCore import Qt

from database.session import get_session
from shared.config.constants import WALKIN_PARTY_ID
from database.models.sale import Sale
from database.models.payment import Payment
from database.models.advance_allocations import AdvanceAllocation
from database.services.payment_service import PaymentService
from database.services.sale_adjustment_service import SaleAdjustmentService

from sqlalchemy import func


class SalePaymentDialog(QDialog):

    def __init__(self, party, user_id, parent=None, print_slip=True):
        super().__init__(parent)
        
        self.party = party
        self.party_id = int(party.id) if party else WALKIN_PARTY_ID
        self.user_id = user_id
        self._last_payment_id: int = 0   # set after each recorded payment, used by Print Receipt
        self._selected_sale_id: int = 0  # set whenever a row is selected, so Print Receipt
                                          # can print that sale's current receipt on demand
        # print_slip kept for backward compatibility with existing callers -
        # no longer drives auto-print (that's been removed entirely); the
        # operator now prints on demand via the Print Receipt button below.

        self.setWindowTitle("Sale Payment")
        self.setMinimumWidth(700)
        self.setMinimumHeight(450)

        layout = QVBoxLayout(self)

        name = getattr(party, "display_name", None) \
            or getattr(party, "name", None) \
            or getattr(party, "full_name", None) \
            or "Customer"
        title = QLabel(f"Settle Sale for: {name}")

        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        from apps.product_management_app.features.profiling.dialogs.cash_shortage_banner import CashShortageBanner
        self.shortage_banner = CashShortageBanner(
            party_id=self.party_id, party_name=name, created_by_user_id=self.user_id,
            reference_type="sale",
        )
        layout.addWidget(self.shortage_banner)

        # Total outstanding balance across EVERY sale shown below, computed
        # fresh each time load_sales() runs - so the operator sees the
        # full picture at a glance before deciding what to pay, not just
        # whatever one row happens to be selected.
        self.total_balance_label = QLabel("Total outstanding: \u20a60.00")
        self.total_balance_label.setStyleSheet(
            "font-size:14px; font-weight:600; color:#b02a2a; padding: 4px 0;")
        layout.addWidget(self.total_balance_label)

        self._total_outstanding = 0.0

        # Two ways to pay: select one specific sale (existing, unchanged
        # behavior), or enter one lump amount and let it distribute
        # automatically across the oldest outstanding sales first, moving
        # to the next once each one's own balance is fully covered.
        mode_box = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.rb_select_row = QRadioButton("Pay selected sale")
        self.rb_lump_sum = QRadioButton("Pay a lump sum across oldest sales")
        self.rb_select_row.setChecked(True)
        self.mode_group.addButton(self.rb_select_row)
        self.mode_group.addButton(self.rb_lump_sum)
        mode_box.addWidget(self.rb_select_row)
        mode_box.addWidget(self.rb_lump_sum)
        mode_box.addStretch(1)
        layout.addLayout(mode_box)

        self.rb_select_row.toggled.connect(self._on_mode_changed)

        # --------------------------------------------------
        # SALES TABLE
        # --------------------------------------------------

        self.table = QTableWidget(0, 5)

        self.table.setHorizontalHeaderLabels([
            "Sale ID",
            "Date",
            "Total",
            "Paid",
            "Balance"
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

        pay_box = QHBoxLayout()

        lbl = QLabel("Payment Amount:")
        self.amount_input = QLineEdit()

        self.amount_input.setPlaceholderText("Enter amount")

        pay_box.addWidget(lbl)
        pay_box.addWidget(self.amount_input)

        layout.addLayout(pay_box)

        # Free-text context for this specific payment - useful when a
        # partial payment gets completed weeks or days later, so whoever
        # handles the completion understands why the amount looks the way
        # it does (a discount, a special arrangement, etc.) without having
        # to ask around.
        note_box = QHBoxLayout()
        note_box.addWidget(QLabel("Note:"))
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText(
            "Optional - e.g. \"10% discount agreed\", \"balance due next week\"")
        note_box.addWidget(self.note_input)
        layout.addLayout(note_box)

        # --------------------------------------------------
        # ACTION BUTTONS
        # --------------------------------------------------

        btns = QHBoxLayout()

        self.btn_record = QPushButton("Record Payment")
        self.btn_print_receipt = QPushButton("Print Receipt")
        self.btn_print_receipt.setEnabled(False)
        self.btn_counter_shortage = QPushButton("Counter Shortage")
        self.btn_close = QPushButton("Close")

        btns.addWidget(self.btn_record)
        btns.addWidget(self.btn_print_receipt)
        btns.addWidget(self.btn_counter_shortage)
        btns.addWidget(self.btn_close)

        layout.addLayout(btns)

        self.btn_close.clicked.connect(self.reject)
        self.btn_record.clicked.connect(self.record_payment)
        self.btn_print_receipt.clicked.connect(self._print_receipt)
        self.btn_counter_shortage.clicked.connect(self._open_counter_shortage)

        # --------------------------------------------------
        # LOAD DATA
        # --------------------------------------------------

        self.load_sales()

    # --------------------------------------------------
    # LOAD SALES WITH BALANCE > 0
    # --------------------------------------------------

    def load_sales(self):

        with get_session() as s:

            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == self.party_id)
                .order_by(Sale.completed_at.asc())
                .all()
            )

            self.table.setRowCount(0)
            total_outstanding = 0.0

            for sale in sales:

                # Reuses SaleAdjustmentService's own formula rather than
                # duplicating it - it already correctly nets out SaleReturn
                # amounts and any manual SaleDiscount adjustments from the
                # Payments section's discount calculator, neither of which
                # this dialog accounted for before (it only ever compared
                # total_amount against payments received).
                nums = SaleAdjustmentService._sale_balance(s, sale)
                total_paid = nums["paid"]
                balance = nums["balance"]

                if balance <= 0:
                    continue

                total_outstanding += balance

                r = self.table.rowCount()
                self.table.insertRow(r)

                cols = [
                    str(sale.id),
                    str(sale.completed_at)[:19],
                    f"{nums['net_total']:,.2f}",
                    f"{total_paid:,.2f}",
                    f"{balance:,.2f}"
                ]

                for c, val in enumerate(cols):

                    it = QTableWidgetItem(val)

                    if c >= 2:
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

                    self.table.setItem(r, c, it)

        self.total_balance_label.setText(f"Total outstanding: \u20a6{total_outstanding:,.2f}")
        self._total_outstanding = total_outstanding

    def autofill_amount(self):

        r = self.table.currentRow()

        if r < 0:
            self._selected_sale_id = 0
            return

        balance = self.table.item(r, 4).text()

        # remove commas
        balance = balance.replace(",", "")

        self.amount_input.setText(balance)

        # Selecting a row is enough to print that sale's current receipt -
        # the operator doesn't need to record a brand new payment first
        # just to print something. Selecting a (possibly different) row
        # also takes priority over any earlier payment recorded this
        # session, keeping "Print Receipt" predictable: it always acts on
        # whatever was most recently selected or just paid.
        self._selected_sale_id = int(self.table.item(r, 0).text())
        self._last_payment_id = 0
        self.btn_print_receipt.setEnabled(True)

    def _on_mode_changed(self, _checked):
        if self.rb_select_row.isChecked():
            self.table.setEnabled(True)
            self.amount_input.setPlaceholderText("Enter amount")
            self.autofill_amount()
        else:
            # Lump-sum mode - row selection no longer drives the amount
            # field; a single figure gets distributed across every
            # outstanding sale instead, oldest first.
            self.table.clearSelection()
            self.table.setEnabled(False)
            self.amount_input.clear()
            self.amount_input.setPlaceholderText(
                f"Enter total to distribute (max \u20a6{self._total_outstanding:,.2f})")
            self._selected_sale_id = 0
            self._last_payment_id = 0
            self.btn_print_receipt.setEnabled(False)

    # --------------------------------------------------
    # RECORD PAYMENT
    # --------------------------------------------------


    def record_payment(self):
        if self.rb_lump_sum.isChecked():
            self._record_lump_sum_payment()
            return

        r = self.table.currentRow()

        if r < 0:
            QMessageBox.information(self, "Select Sale", "Select a sale first.")
            return

        try:
            amount = float(self.amount_input.text())
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Enter a valid amount.")
            return

        if amount <= 0:
            QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than zero.")
            return

        sale_id = int(self.table.item(r, 0).text())

        balance = float(self.table.item(r, 4).text().replace(",", ""))

        if amount > balance:
            QMessageBox.warning(
                self,
                "Overpayment",
                f"Amount exceeds balance ({balance:,.2f})."
            )
            return

        # Offer the customer's advance before taking cash (no auto deduction).
        from apps.product_management_app.features.profiling.dialogs.advance_choice_dialog import prompt_and_apply_advance
        _pname = getattr(self.party, "display_name", "") or getattr(self.party, "full_name", "") or ""
        new_balance = prompt_and_apply_advance(
            self,
            party_id=self.party_id,
            entity_type="sale",
            entity_id=sale_id,
            balance=balance,
            user_id=self.user_id,
            party_name=_pname,
        )
        if new_balance < balance:
            balance = new_balance
            if amount > balance:
                amount = balance
            if amount <= 0:
                QMessageBox.information(
                    self, "Advance Applied",
                    "The advance covered the balance. Nothing left to pay."
                )
                self.load_sales()
                self.amount_input.clear()
                return

        try:

            # -----------------------------------------
            # RECORD PAYMENT
            # -----------------------------------------

            payment_id = PaymentService.record_sale_payment(
                sale_id=sale_id,
                party_id=self.party_id,
                amount=amount,
                created_by_user_id=self.user_id,
                method="manual",
                notes=self.note_input.text().strip() or "Manual payment from Payment Tab"
            )

            # -----------------------------------------
            # No auto-print - track this payment so Print Receipt (enabled
            # right below) can print it whenever the operator is ready.
            # -----------------------------------------

            self._last_payment_id = int(payment_id or 0)
            self.btn_print_receipt.setEnabled(self._last_payment_id > 0)

            QMessageBox.information(self, "Success", "Payment recorded successfully.")

            self.load_sales()
            self.amount_input.clear()
            self.note_input.clear()

        except Exception as e:

            QMessageBox.critical(self, "Payment Failed", str(e))

    def _record_lump_sum_payment(self):
        try:
            amount = float(self.amount_input.text())
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Enter a valid amount.")
            return

        if amount <= 0:
            QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than zero.")
            return

        # Re-fetch fresh, current balances right before distributing - not
        # whatever the table showed when it was last loaded, since another
        # payment could have landed on one of these sales in the meantime.
        with get_session() as s:
            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == self.party_id)
                .order_by(Sale.completed_at.asc())  # oldest first
                .all()
            )
            outstanding = []
            total = 0.0
            for sale in sales:
                nums = SaleAdjustmentService._sale_balance(s, sale)
                if nums["balance"] > 0:
                    outstanding.append((int(sale.id), nums["balance"]))
                    total += nums["balance"]

        if amount > total:
            QMessageBox.warning(
                self, "Amount Too High",
                f"\u20a6{amount:,.2f} exceeds the total outstanding balance "
                f"(\u20a6{total:,.2f}) across all of this customer's sales."
            )
            return

        confirm = QMessageBox.question(
            self, "Confirm Lump Sum Payment",
            f"Distribute \u20a6{amount:,.2f} across the oldest outstanding "
            f"sales, one at a time, until fully applied?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        note = self.note_input.text().strip()
        remaining = amount
        applied = []
        try:
            for sale_id, balance in outstanding:
                if remaining <= 0:
                    break
                pay_this = min(remaining, balance)
                PaymentService.record_sale_payment(
                    sale_id=sale_id,
                    party_id=self.party_id,
                    amount=pay_this,
                    created_by_user_id=self.user_id,
                    method="manual",
                    notes=(
                        f"Part of a \u20a6{amount:,.2f} lump-sum payment "
                        f"across multiple sales"
                        + (f" - {note}" if note else "")
                    ),
                )
                applied.append((sale_id, pay_this))
                remaining -= pay_this
        except Exception as e:
            # Each record_sale_payment call commits independently, so
            # anything already applied above stays applied even though
            # this one failed - the summary reflects exactly that.
            summary = "\n".join(f"  Sale #{sid}: \u20a6{amt:,.2f}" for sid, amt in applied)
            QMessageBox.critical(
                self, "Distribution Interrupted",
                f"An error occurred partway through: {e}\n\n"
                f"Successfully applied before the error:\n{summary or '  (none)'}"
            )
            self.load_sales()
            self.amount_input.clear()
            self.note_input.clear()
            return

        summary = "\n".join(f"  Sale #{sid}: \u20a6{amt:,.2f}" for sid, amt in applied)
        QMessageBox.information(
            self, "Payment Distributed",
            f"\u20a6{amount:,.2f} distributed across {len(applied)} sale(s):\n\n{summary}"
        )

        self.load_sales()
        self.amount_input.clear()
        self.note_input.clear()

    # --------------------------------------------------
    # PRINT RECEIPT (manual, on demand - no auto-print)
    # --------------------------------------------------

    def _open_counter_shortage(self):
        from apps.product_management_app.features.profiling.dialogs.shortage_activity_picker_dialog import (
            ShortageActivityPickerDialog,
        )
        name = getattr(self.party, "display_name", None) or getattr(self.party, "full_name", None) or "Customer"
        dlg = ShortageActivityPickerDialog(
            party_id=self.party_id, party_name=name, reference_type="sale",
            created_by_user_id=self.user_id, parent=self,
        )
        dlg.exec_()
        self.shortage_banner.refresh()

    def _print_receipt(self):
        if self._last_payment_id > 0:
            # A fresh payment was just recorded - print that specific
            # transaction's receipt, the existing, more precise behavior.
            try:
                from apps.product_management_app.features.receipts_v2 import (
                    print_receipt as print_receipt_doc, PAYMENT, MODE_FULL,
                )
                print_receipt_doc(self, PAYMENT, self._last_payment_id, MODE_FULL)
            except Exception as e:
                QMessageBox.warning(self, "Print Error", f"Could not print receipt:\n{e}")
            return

        if self._selected_sale_id > 0:
            # No new payment recorded this session - print the SALE's own
            # current receipt instead, reflecting its balance as it stands
            # right now (including any manual discount applied elsewhere).
            try:
                from apps.product_management_app.features.receipts_v2 import (
                    print_receipt as print_receipt_doc, SALE, MODE_FULL,
                )
                print_receipt_doc(self, SALE, self._selected_sale_id, MODE_FULL)
            except Exception as e:
                QMessageBox.warning(self, "Print Error", f"Could not print receipt:\n{e}")
            return

        QMessageBox.information(
            self, "Print Receipt", "Select a sale first, or record a payment to print."
        )