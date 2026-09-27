# apps/product_management_app/features/cashier/dialogs/intake_start_dialog.py
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QGroupBox, QMessageBox
)
from apps.product_management_app.app_state import state


class IntakeStartDialog(QDialog):
    """
    Step 1 - a genuine, explicit separation of concern for who this intake
    is from:

      Existing Supplier  - picked from the REAL suppliers list only, never
                            matched or merged against customers/walk-ins.
      Existing Customer  - buying from a specific, known customer (e.g.
                            via their own profile).
      New Customer       - typed name/phone, creates a genuine, dedicated
                            party_type="customer" row (is_customer=True),
                            so it actually shows up in the real Customers
                            list.
      Walk-in            - typed name/phone, creates (or reuses, by phone
                            only) a genuine party_type="walkin" row via
                            state.get_or_lazy_create_walkin - the same
                            casual-trader lane the rest of the app uses.

    "New Supplier" has been removed on purpose: creating a brand-new
    supplier is never actually needed from this screen, and it used to be
    the source of a real bug - the old "Supplier/Internal" option was
    labeled as a supplier but silently created a "customer" or "walkin"
    party instead, never a real supplier at all, which is exactly what
    caused a brand-new party to incorrectly show up in the Customers list
    too, once PurchaseService.record flagged is_supplier=True on top of
    that mistaken party_type. If a genuinely new supplier ever needs
    onboarding, that should be its own explicit action on the Suppliers
    screen - not a side door on an intake dialog.

    Returns a context dict used by the smart dialogs.
    """

    # Combo display text -> internal mode key
    _MODE_MAP = {
        "existing supplier": "existing_supplier",
        "existing customer": "existing_customer",
        "new customer": "new_customer",
        "walk-in": "walkin",
    }

    def __init__(self, parent=None, preselect_customer_id: int = 0, preselect_customer_name: str = ""):
        super().__init__(parent)
        self.setModal(True)
        self.resize(540, 320)
        self.setWindowTitle("New Intake (Guided)")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Start Intake")
        title.setObjectName("Title")
        root.addWidget(title)

        # Source group
        gb = QGroupBox("Source")
        root.addWidget(gb)
        gl = QVBoxLayout(gb)

        r1 = QHBoxLayout()
        gl.addLayout(r1)
        r1.addWidget(QLabel("Source type:"))
        self.source_type = QComboBox()
        self.source_type.addItems(["Existing Supplier", "Existing Customer", "New Customer", "Walk-in"])
        r1.addWidget(self.source_type, 1)

        r2 = QHBoxLayout()
        gl.addLayout(r2)
        r2.addWidget(QLabel("Customer:"))
        self.customer_display = QLineEdit()
        self.customer_display.setReadOnly(True)
        self.customer_display.setPlaceholderText("No customer selected")
        r2.addWidget(self.customer_display, 1)
        self.btn_pick_customer = QPushButton("Select…")
        r2.addWidget(self.btn_pick_customer)
        self._selected_customer_id = 0
        self._selected_customer_name = ""
        self._selected_customer_phone = ""

        r2b = QHBoxLayout()
        gl.addLayout(r2b)
        r2b.addWidget(QLabel("Supplier:"))
        self.supplier_display = QLineEdit()
        self.supplier_display.setReadOnly(True)
        self.supplier_display.setPlaceholderText("No supplier selected")
        r2b.addWidget(self.supplier_display, 1)
        self.btn_pick_supplier = QPushButton("Select…")
        r2b.addWidget(self.btn_pick_supplier)
        self._selected_supplier_id = 0
        self._selected_supplier_name = ""

        # Shared typed name/phone rows - used for BOTH "New Customer" and
        # "Walk-in" (never for a supplier). The label text switches
        # depending on which of the two is active, in _sync_source_ui.
        r3 = QHBoxLayout()
        gl.addLayout(r3)
        self.lbl_name = QLabel("New customer name:")
        r3.addWidget(self.lbl_name)
        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("Name")
        r3.addWidget(self.name_in, 1)

        r4 = QHBoxLayout()
        gl.addLayout(r4)
        r4.addWidget(QLabel("Phone (optional):"))
        self.phone_in = QLineEdit()
        self.phone_in.setPlaceholderText("e.g. 0706…")
        r4.addWidget(self.phone_in, 1)

        # Intake type
        r5 = QHBoxLayout()
        root.addLayout(r5)
        r5.addWidget(QLabel("Intake type:"))
        self.intake_type = QComboBox()
        self.intake_type.addItems(["Crops Intake", "Fertilizer Intake"])
        r5.addWidget(self.intake_type, 1)

        # Buttons
        btns = QHBoxLayout()
        root.addLayout(btns)
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("PrimaryButton")
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_next)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_next.clicked.connect(self._next)
        self.btn_pick_customer.clicked.connect(self._pick_customer)
        self.btn_pick_supplier.clicked.connect(self._pick_supplier)

        self.source_type.currentTextChanged.connect(self._sync_source_ui)

        # Convenience default when opened from a specific customer's own
        # profile - "this customer" is pre-selected, since that's almost
        # always the intent of clicking that button. But the choice stays
        # fully visible and changeable right here, rather than being
        # silently assumed and hardcoded before the operator ever sees a
        # dialog at all - a purchase's real source should always be an
        # explicit, deliberate decision, every time, regardless of where
        # in the app it gets initiated from.
        if preselect_customer_id > 0:
            self.source_type.setCurrentText("Existing Customer")
            self._selected_customer_id = int(preselect_customer_id)
            self._selected_customer_name = preselect_customer_name or ""
            self._selected_customer_phone = self._lookup_party_phone(preselect_customer_id)
            self.customer_display.setText(self._selected_customer_name)

        self._sync_source_ui()
        self._source_party_id = 0

    def _lookup_party_phone(self, party_id: int) -> str:
        from database.session import get_session
        from database.models.party import Party
        with get_session() as s:
            p = s.get(Party, int(party_id))
            return (p.phone or "") if p else ""

    def _pick_customer(self):
        from apps.product_management_app.features.cashier.dialogs.party_search_picker_dialog import (
            PartySearchPickerDialog,
        )
        dlg = PartySearchPickerDialog(role_flag="is_customer", title="Select Customer", parent=self)
        if dlg.exec_() == dlg.Accepted and dlg.selected_party_id:
            self._selected_customer_id = dlg.selected_party_id
            self._selected_customer_name = dlg.selected_party_name or ""
            self._selected_customer_phone = self._lookup_party_phone(dlg.selected_party_id)
            self.customer_display.setText(self._selected_customer_name)

    def _pick_supplier(self):
        from apps.product_management_app.features.cashier.dialogs.party_search_picker_dialog import (
            PartySearchPickerDialog,
        )
        dlg = PartySearchPickerDialog(role_flag="is_supplier", title="Select Supplier", parent=self)
        if dlg.exec_() == dlg.Accepted and dlg.selected_party_id:
            self._selected_supplier_id = dlg.selected_party_id
            self._selected_supplier_name = dlg.selected_party_name or ""
            self.supplier_display.setText(self._selected_supplier_name)

    def _sync_source_ui(self):
        mode = self._MODE_MAP.get(self.source_type.currentText().strip().lower(), "")
        is_existing_customer = (mode == "existing_customer")
        is_existing_supplier = (mode == "existing_supplier")
        is_new_customer = (mode == "new_customer")
        is_walkin = (mode == "walkin")

        self.customer_display.setEnabled(is_existing_customer)
        self.btn_pick_customer.setEnabled(is_existing_customer)
        self.supplier_display.setEnabled(is_existing_supplier)
        self.btn_pick_supplier.setEnabled(is_existing_supplier)

        typed = is_new_customer or is_walkin
        self.name_in.setEnabled(typed)
        self.phone_in.setEnabled(typed)
        self.lbl_name.setText("Walk-in name:" if is_walkin else "New customer name:")

    def context(self) -> dict:
        mode = self._MODE_MAP.get(self.source_type.currentText().strip().lower(), "")
        is_existing_customer = (mode == "existing_customer")
        is_existing_supplier = (mode == "existing_supplier")
        is_new_customer = (mode == "new_customer")
        is_walkin = (mode == "walkin")

        customer_id = int(self._selected_customer_id) if (is_existing_customer and self._selected_customer_id) else 0
        supplier_id = int(self._selected_supplier_id) if (is_existing_supplier and self._selected_supplier_id) else 0

        cust_name = self._selected_customer_name if customer_id else ""
        cust_phone = self._selected_customer_phone if customer_id else ""

        it = "crops" if self.intake_type.currentIndex() == 0 else "fertilizer"

        typed_name = self.name_in.text().strip() if (is_new_customer or is_walkin) else ""
        typed_phone = self.phone_in.text().strip() if (is_new_customer or is_walkin) else ""

        ctx = {
            "intake_type": it,
            "source_type": mode,
            "customer_id": customer_id,
            "customer_name": cust_name,
            "customer_phone": cust_phone,
            "existing_supplier_id": supplier_id,
            "source_name": typed_name,
            "source_phone": typed_phone,
        }

        ctx["source_party_id"] = int(getattr(self, "_source_party_id", 0) or 0)
        return ctx

    def _next(self):
        ctx = self.context()
        mode = ctx["source_type"]

        if mode == "existing_customer" and not ctx["customer_id"]:
            QMessageBox.warning(self, "Source", "Select a customer.")
            return
        if mode == "existing_supplier" and not ctx["existing_supplier_id"]:
            QMessageBox.warning(self, "Source", "Select a supplier.")
            return
        if mode == "new_customer" and not ctx["source_name"]:
            QMessageBox.warning(self, "Source", "Enter the new customer's name.")
            return
        if mode == "walkin" and not ctx["source_name"]:
            QMessageBox.warning(self, "Source", "Enter the walk-in's name.")
            return

        if mode == "existing_customer":
            self._source_party_id = int(ctx["customer_id"])

        elif mode == "existing_supplier":
            self._source_party_id = int(ctx["existing_supplier_id"])

        elif mode == "new_customer":
            nm = ctx["source_name"]
            ph = ctx["source_phone"]
            # Genuinely creates a real party_type="customer" row
            # (is_customer=True), so it actually shows up in the real
            # Customers list. Always a brand-new row - unlike the walk-in
            # lane, this never tries to dedupe/reuse an existing party by
            # phone, since "New Customer" is an explicit onboarding
            # action, not a lookup.
            new_customer = state.create_customer(full_name=nm, phone=ph)
            self._source_party_id = int(new_customer.id) if new_customer else 0
            if self._source_party_id <= 0:
                QMessageBox.critical(self, "Source", "Could not create the new customer.")
                return

        elif mode == "walkin":
            nm = ctx["source_name"]
            ph = ctx["source_phone"]
            # Genuinely creates (or reuses, by phone only) a real
            # party_type="walkin" row - the same casual-trader lane the
            # rest of the app uses (see AppState.get_or_lazy_create_walkin).
            self._source_party_id = int(state.get_or_lazy_create_walkin(nm, ph))

        else:
            self._source_party_id = int(state.get_internal_supplier_party_id())

        self.accept()