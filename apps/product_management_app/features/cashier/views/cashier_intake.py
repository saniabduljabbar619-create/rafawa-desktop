# apps/product_management_app/features/cashier/views/cashier_intake.py
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from apps.product_management_app.features.cashier.dialogs.purchase_payment_prompt_dialog import PurchasePaymentPromptDialog

from apps.product_management_app.features.receipts_v2 import (
    print_full_then_optional_delivery, PURCHASE, SALE,
)
from datetime import datetime
from shared.uix.layout.scrollable import make_scrollable_page


class CashierIntakeView(QWidget):
    def __init__(self):
        super().__init__()

        root = make_scrollable_page(self, margins=(12, 12, 12, 12), spacing=10)

        header = QHBoxLayout()
        root.addLayout(header)

        title = QLabel("Intake (Delivery)")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        
        self.btn_swap = QPushButton("Swap / Exchange")
        self.btn_swap.setToolTip("A customer brings goods and takes goods in the same visit")
        header.addWidget(self.btn_swap)

        self.btn_new = QPushButton("New Intake")
        self.btn_new.setObjectName("PrimaryButton")
        self.btn_new.setAutoDefault(False)   # <-- here, after creation
        self.btn_new.setDefault(False)
        header.addWidget(self.btn_new)
        print("DEBUG btn_new:",
            "enabled=", self.btn_new.isEnabled(),
            "objectName=", repr(self.btn_new.objectName()),
            "localStyleSheet=", repr(self.btn_new.styleSheet()))

        # Search
        row = QHBoxLayout()
        root.addLayout(row)

        row.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Product / SKU / Source…")
        row.addWidget(self.search, 1)

        self.btn_refresh = QPushButton("Refresh")
        row.addWidget(self.btn_refresh)

        # Table
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "DT", "Ref", "Customer", "Product", "SKU", "Qty Added", "Unit Price", "Source", "Processed By"
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        # Wiring
        self.btn_new.clicked.connect(self._new_intake)
        self.btn_swap.clicked.connect(self._swap)
        self.btn_refresh.clicked.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)

        self.refresh()

    def _new_intake(self):
        from PySide2.QtWidgets import QMessageBox
        from apps.product_management_app.features.cashier.dialogs.intake_start_dialog import IntakeStartDialog
        from apps.product_management_app.features.cashier.dialogs.intake_cart_dialog import IntakeCartDialog

        # Step 1: who is this intake from (Existing Supplier / Existing
        # Customer / New Customer / Walk-in - see IntakeStartDialog for
        # why "New Supplier" no longer exists here).
        start = IntakeStartDialog(self)
        if start.exec_() != start.Accepted:
            return

        ctx = start.context() or {}
        intake_type = str(ctx.get("intake_type") or "").strip().lower()
        if intake_type not in ("fertilizer", "crops"):
            QMessageBox.warning(self, "Intake", "Select intake type (Crops or Fertilizer).")
            return

        party_id = int(ctx.get("source_party_id") or 0)
        if party_id <= 0:
            QMessageBox.warning(self, "Intake", "Select a valid source (customer, supplier, or walk-in).")
            return

        source_type = str(ctx.get("source_type") or "").strip()
        source_name = str(ctx.get("source_name") or ctx.get("customer_name") or "").strip()
        source_phone = str(ctx.get("source_phone") or ctx.get("customer_phone") or "").strip()
        processed_by = str(getattr(state, "operator_name", "") or "").strip() or "operator"

        # A purchase FROM an existing/new CUSTOMER or a walk-in must never
        # silently flag that party as a supplier too - only an actual,
        # deliberately-picked EXISTING supplier does that automatically.
        # (Walk-ins are already skipped inside PurchaseService._ensure_role
        # regardless of this flag; it matters for customer/new-customer.)
        auto_flag_supplier = (source_type == "existing_supplier")

        # Step 2: cart - add as many products as this source is delivering
        # (Masar, Dawa, Wake, Waken Soya, ...), review, then finalize.
        display_label = source_name or ctx.get("customer_name") or "Selected source"
        cart = IntakeCartDialog(self, intake_type=intake_type, source_label=display_label)
        if cart.exec_() != cart.Accepted:
            return

        cart_items = cart.items()
        if not cart_items:
            return

        notes = (
            f"type={intake_type}; "
            f"source_type={source_type}; "
            f"source={source_name}; "
            f"source_phone={source_phone}; "
            f"processed_by={processed_by}"
        )

        header = {
            "supplier_party_id": party_id,
            "created_by_user_id": state.require_user_id(),
            "note": notes,
            "walkin_name": source_name if source_type == "walkin" else "",
            "auto_flag_supplier": auto_flag_supplier,
        }

        try:
            # Whole cart -> ONE purchase, one reference, one total.
            res = state.record_intake_multi_db(header, cart_items)
            purchase_id = int(res["purchase_id"])
            purchase_total = float(res.get("total_amount") or 0.0)

            from database.models.party import Party
            from database.session import get_session

            with get_session() as s:
                party = s.query(Party).get(int(party_id))

            # Ask whether to apply the party's advance (no auto deduction).
            from apps.product_management_app.features.profiling.dialogs.advance_choice_dialog import prompt_and_apply_advance
            prompt_and_apply_advance(
                self,
                party_id=int(party_id),
                entity_type="purchase",
                entity_id=purchase_id,
                balance=purchase_total,
                user_id=state.require_user_id(),
                party_name=(party.display_name if party else ""),
            )

            # If they owe us for unpaid sales, offer to settle those with these goods.
            from apps.product_management_app.features.profiling.dialogs.offset_choice_dialog import (
                prompt_and_apply_offset,
            )
            still_owed = prompt_and_apply_offset(
                self, party_id=int(party_id), purchase_id=purchase_id,
                party_name=(party.display_name if party else ""),
            )

            if still_owed > 0.005:
                from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import PurchasePaymentDialog

                pay = PurchasePaymentDialog(
                    party=party,
                    user_id=state.require_user_id(),
                    parent=self,
                    print_slip=False
                )
                pay.exec_()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Intake Failed",
                f"Could not record intake:\n{e}"
            )
            return

        # --------------------------------------------------
        # PRINT: full intake receipt (all cart lines), then ask about a
        # delivery note. [Option 2]
        # --------------------------------------------------
        try:
            if purchase_id > 0:
                print_full_then_optional_delivery(self, PURCHASE, purchase_id)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Receipt Error",
                f"Intake recorded but receipt printing failed:\n{e}"
            )
        self.refresh()

    def _swap(self):
        from PySide2.QtWidgets import QMessageBox
        from apps.product_management_app.features.cashier.dialogs.party_search_picker_dialog import (
            PartySearchPickerDialog,
        )
        from apps.product_management_app.features.cashier.dialogs.swap_dialog import SwapDialog

        picker = PartySearchPickerDialog(role_flag="any", title="Swap with which customer or supplier?", parent=self)
        if picker.exec_() != picker.Accepted or not picker.selected_party_id:
            return
        dlg = SwapDialog(self, party_id=int(picker.selected_party_id), party_name=picker.selected_party_name or "")
        if dlg.exec_() != dlg.Accepted or not dlg.result_data:
            return
        r = dlg.result_data
        if r["we_owe_him"] > 0:
            tail = f"We still owe him {r['we_owe_him']:,.2f} (pay it from the Debt Center or Payments)."
        elif r["he_owes_us"] > 0:
            tail = f"He still owes us {r['he_owes_us']:,.2f} (collect it from the Debt Center or Payments)."
        else:
            tail = "Even - nothing left to pay."
        QMessageBox.information(
            self, "Swap recorded",
            f"He brought {r['purchase_total']:,.2f} (PUR-{r['purchase_id']}) and took "
            f"{r['sale_total']:,.2f} (SAL-{r['sale_id']}).\n{r['offset']:,.2f} was set off.\n\n{tail}")
        try:
            print_full_then_optional_delivery(self, PURCHASE, int(r["purchase_id"]))     # goods he brought
            print_full_then_optional_delivery(self, SALE, int(r["sale_id"]))            # goods he took
        except Exception as e:
            QMessageBox.warning(self, "Receipt Error", f"Swap recorded but the receipt failed:\n{e}")
        self.refresh()

    def refresh(self):
        q = self.search.text().strip().lower()

        from database.services.intake_service import list_intakes_db
        rows = list_intakes_db(
            limit=500,
            session_user_id=state.require_user_id(),
        )

        def match(r) -> bool:
            if not q:
                return True
            product = str(r.get("product_name", "")).lower()
            sku = str(r.get("sku", "")).lower()
            source = str(r.get("source_name", "")).lower()
            return (q in product) or (q in sku) or (q in source)

        rows = [r for r in rows if match(r)]

        self.table.setRowCount(0)
        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            dt = str(r.get("dt", "") or "")
            ref = str(r.get("ref", "") or "")
            cust = str(r.get("customer", "") or "")
            product = str(r.get("product_name", "") or "")
            sku = str(r.get("sku", "") or "")
            qty = r.get("qty_kg", "")
            price = r.get("unit_price", "")
            source = str(r.get("source_name", "") or "")
            processed = str(r.get("processed_by", "") or "")

            vals = [
                dt, ref, cust, product, sku,
                f"{float(qty):,.2f}" if qty != "" else "",
                f"{float(price):,.2f}" if price != "" else "",
                source, processed
            ]

            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, c, it)