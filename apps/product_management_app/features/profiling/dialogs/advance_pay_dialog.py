# apps\product_management_app\features\profiling\dialogs\advance_pay_dialog.py

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QMessageBox
)

from database.services.advance_service import AdvanceService
from apps.product_management_app.features.receipts_v2 import print_receipt, ADVANCE, MODE_FULL

class AdvancePayDialog(QDialog):

    def __init__(self, party, user_id, parent=None):
        super().__init__(parent)

        self.party = party
        self.user_id = user_id
        self._last_advance_id: int = 0   # set after a successful record, used by Print Receipt

        self.setWindowTitle("Advance Pay Center")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        # ----------------------------------------
        # HEADER
        # ----------------------------------------

        name = getattr(party, "display_name", None) \
            or getattr(party, "full_name", None) \
            or getattr(party, "name", None) \
            or "Customer"

        title = QLabel(f"Advance Pay Center for: {name}")

        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        # ----------------------------------------
        # DIRECTION
        # ----------------------------------------

        layout.addWidget(QLabel("Direction"))

        self.direction = QComboBox()
        self.direction.addItems([
            "Customer Deposit (Advance IN)",
            "Advance Given to Customer (Advance OUT)"
        ])
        layout.addWidget(self.direction)

        # ----------------------------------------
        # LEDGER (context only)
        # ----------------------------------------

        layout.addWidget(QLabel("Ledger (context only)"))

        self.ledger = QComboBox()
        self.ledger.addItems(["crops", "fertilizers"])
        layout.addWidget(self.ledger)

        # ----------------------------------------
        # AMOUNT
        # ----------------------------------------

        layout.addWidget(QLabel("Amount"))

        self.amount = QLineEdit()
        self.amount.setPlaceholderText("Enter amount")
        layout.addWidget(self.amount)

        # ----------------------------------------
        # NOTES
        # ----------------------------------------

        layout.addWidget(QLabel("Notes"))

        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Optional context")
        layout.addWidget(self.notes)

        # ----------------------------------------
        # ACTION BUTTONS
        # ----------------------------------------

        self.btn_record = QPushButton("Record Advance")
        layout.addWidget(self.btn_record)

        # No longer auto-printed - the operator prints on demand, as many
        # times as needed, only after a successful record. Disabled until
        # then so there is nothing to print before an advance actually exists.
        btn_row = QHBoxLayout()
        self.btn_print_receipt = QPushButton("Print Receipt")
        self.btn_print_receipt.setEnabled(False)
        self.btn_close = QPushButton("Close")
        btn_row.addWidget(self.btn_print_receipt)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.btn_record.clicked.connect(self.record_advance)
        self.btn_print_receipt.clicked.connect(self._print_receipt)
        self.btn_close.clicked.connect(self.accept)

    # -------------------------------------------------
    # RECORD ADVANCE
    # -------------------------------------------------

    def record_advance(self):

        try:
            amount = float(self.amount.text())
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Enter a valid amount.")
            return

        if amount <= 0:
            QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than zero.")
            return

        direction_index = self.direction.currentIndex()
        ledger = self.ledger.currentText()
        notes = self.notes.text().strip()

        try:

            # -----------------------------------------
            # RECORD ADVANCE
            # -----------------------------------------

            if direction_index == 0:

                advance_id = AdvanceService.record_advance_in(
                    party_id=int(self.party.id),
                    amount=float(amount),
                    created_by_user_id=self.user_id,
                    ledger=ledger,
                    notes=notes
                )

            else:

                advance_id = AdvanceService.record_advance_out(
                    party_id=int(self.party.id),
                    amount=float(amount),
                    created_by_user_id=self.user_id,
                    ledger=ledger,
                    notes=notes
                )

            self._last_advance_id = int(advance_id or 0)

            # The advance is saved. Lock the form NOW - before anything else can go wrong - so
            # this entry can never be submitted a second time (a retry after a later error used
            # to record the same advance again, doubling the Advance OUT/IN balance).
            self.btn_record.setEnabled(False)
            self.direction.setEnabled(False)
            self.ledger.setEnabled(False)
            self.amount.setEnabled(False)
            self.notes.setEnabled(False)
            self.btn_print_receipt.setEnabled(self._last_advance_id > 0)

            # -----------------------------------------
            # OFFER TO SETTLE AGAINST EXISTING BALANCE
            # -----------------------------------------
            # A freshly recorded advance sits unapplied until something
            # actively allocates it - PaymentCenter only does that at
            # purchase/sale creation time. If this party already has a
            # real outstanding balance on the opposite side, ask right
            # here; otherwise the advance just sits unused while the
            # Purchase/Sale Payment dialogs keep showing the full,
            # un-netted debt with no way to reconcile it from this screen.
            try:
                self._offer_settlement(direction_index, amount)
            except Exception as e:
                QMessageBox.warning(
                    self, "Advance Recorded - Not Applied",
                    f"The advance was recorded, but it could not be applied to the outstanding "
                    f"balance:\n{e}\n\nIt is NOT recorded twice - it will be offered "
                    f"again the next time you make a payment for this party.")
                return

            QMessageBox.information(self, "Success", "Advance recorded successfully.")

        except Exception as e:

            QMessageBox.critical(self, "Advance Failed", str(e))

    # -------------------------------------------------
    # OFFER TO SETTLE AGAINST EXISTING BALANCE
    # -------------------------------------------------

    def _offer_settlement(self, direction_index: int, recorded: float = 0.0):
        """If this party owes on the opposite side, let the user choose where the advance goes (never automatic)."""
        from apps.product_management_app.features.profiling.dialogs.advance_apply_dialog import (
            choose_and_apply_advance,
        )

        name = getattr(self.party, "display_name", None) \
            or getattr(self.party, "full_name", None) \
            or getattr(self.party, "name", None) \
            or "This party"
        kind = "purchase" if direction_index == 1 else "sale"      # Advance OUT -> purchases, IN -> sales
        choose_and_apply_advance(self, party_id=int(self.party.id), kind=kind, user_id=self.user_id,
                                 party_name=name, suggest=recorded or None)

    # -------------------------------------------------
    # PRINT RECEIPT (manual, on demand - no auto-print)
    # -------------------------------------------------

    def _print_receipt(self):
        if self._last_advance_id <= 0:
            QMessageBox.information(
                self, "Print Receipt", "No recorded advance to print a receipt for."
            )
            return
        try:
            print_receipt(self, ADVANCE, self._last_advance_id, MODE_FULL)
        except Exception as e:
            QMessageBox.warning(self, "Print Error", f"Could not print receipt:\n{e}")