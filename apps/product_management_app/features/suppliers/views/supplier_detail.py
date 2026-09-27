# apps/product_management_app/features/suppliers/views/supplier_detail.py
# -*- coding: utf-8 -*-
"""
Supplier / trading partner profile.

A party here is no longer strictly "a supplier" - independent is_customer /
is_supplier flags mean the same party can be bought from AND sold to. This
view surfaces both directions: Payable/Receivable cards, Purchases AND Sales
tabs, a unified Activity timeline, and both "Pay Supplier" (we owe them) and
"Collect from Supplier" (they owe us) actions. Money logic lives in
PurchasePaymentDialog / SalePaymentDialog (both pre-existing, reused
unchanged) and in PurchaseService / SaleService for the Bulk Purchase intake
dialogs (also pre-existing, reused unchanged - just pre-bound to this party
so the operator never has to re-pick who they're buying from).
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QTableWidget, QTableWidgetItem, QGroupBox, QHeaderView, QMessageBox,
    QPushButton, QMenu
)
from PySide2.QtCore import Qt

from database.services.party_balance_service import PartyBalanceService
from database.services.supplier_profile_service import SupplierProfileService
from apps.product_management_app.app_state import state
from apps.product_management_app.features.suppliers.views.supplier_activity_dialogs import (
    SupplierPurchaseDetailDialog, SupplierPaymentDetailDialog, SupplierSaleDetailDialog,
)
from apps.product_management_app.features.suppliers.views.supplier_new_sale_dialog import (
    SupplierNewSaleDialog,
)

RED = "#c0392b"
GREEN = "#27ae60"
GREY = "#6b7280"
INK = "#111827"


def money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class SupplierProfileView(QWidget):

    def __init__(self, supplier_party_id: int, parent=None):
        super().__init__(parent)
        self.supplier_party_id = int(supplier_party_id or 0)
        self.session_user_id = state.require_user_id()
        self.role = str((state.session_user or {}).get("role") or "cashier")
        self._activity_rows = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---------------- header ----------------
        hdr = SupplierProfileService.header(self.supplier_party_id)
        header = QHBoxLayout()
        title = QLabel(hdr["name"])
        title.setStyleSheet("font-size:18px; font-weight:700;")
        header.addWidget(title)

        if hdr.get("is_customer"):
            badge = QLabel("Also a Customer")
            badge.setStyleSheet(
                "background:#eef2ff; color:#4338ca; border-radius:10px;"
                " padding:2px 10px; font-weight:700; font-size:11px;"
            )
            header.addWidget(badge)

        header.addStretch(1)
        meta = QLabel(f"ID: {hdr['id']}   |   Phone: {hdr['phone'] or '-'}   "
                      f"|   Address: {hdr['address'] or '-'}")
        meta.setStyleSheet(f"color:{GREY};")
        header.addWidget(meta)

        self.btn_bulk = QPushButton("Bulk Purchase")
        self.btn_bulk.setStyleSheet(
            f"QPushButton {{ background:{INK}; color:#fff; border:none;"
            f" border-radius:8px; padding:6px 16px; font-weight:800; }}")
        self.btn_bulk.clicked.connect(self._open_bulk_purchase_menu)
        header.addWidget(self.btn_bulk)

        self.btn_new_sale = QPushButton("New Sale")
        self.btn_new_sale.setStyleSheet(
            f"QPushButton {{ background:#2563eb; color:#fff; border:none;"
            f" border-radius:8px; padding:6px 16px; font-weight:800; }}")
        self.btn_new_sale.clicked.connect(self._open_new_sale)
        header.addWidget(self.btn_new_sale)

        self.btn_collect = QPushButton("Collect from Supplier")
        self.btn_collect.setStyleSheet(
            f"QPushButton {{ background:{GREEN}; color:#fff; border:none;"
            f" border-radius:8px; padding:6px 16px; font-weight:800; }}")
        self.btn_collect.clicked.connect(self._open_collect_payment)
        header.addWidget(self.btn_collect)

        self.btn_pay = QPushButton("Pay Supplier")
        self.btn_pay.setStyleSheet(
            f"QPushButton {{ background:{RED}; color:#fff; border:none;"
            f" border-radius:8px; padding:6px 16px; font-weight:800; }}")
        self.btn_pay.clicked.connect(self._open_pay_supplier)
        header.addWidget(self.btn_pay)
        layout.addLayout(header)

        # ---------------- balance cards ----------------
        layout.addWidget(self._balance_box())

        # ---------------- tabs ----------------
        tabs = QTabWidget()
        self.tbl_activity = self._make_table(
            ["When", "Kind", "Reference", "Detail", "Amount", "Status"])
        self.tbl_purchases = self._make_table(
            ["When", "Purchase No", "Type", "For Sale", "Amount", "Status"])
        self.tbl_sales = self._make_table(
            ["When", "Sale No", "Amount", "Status"])
        self.tbl_payments = self._make_table(
            ["When", "Direction", "For", "Amount", "Method"])
        self.tbl_activity.cellDoubleClicked.connect(self._open_activity_detail)
        self.tbl_purchases.cellDoubleClicked.connect(self._open_purchase_detail)
        self.tbl_sales.cellDoubleClicked.connect(self._open_sale_detail)
        self.tbl_payments.cellDoubleClicked.connect(self._open_payment_detail)
        # Activity first - it is the "everything that happened" umbrella view.
        tabs.addTab(self._wrap(self.tbl_activity), "Activity")
        tabs.addTab(self._wrap(self.tbl_purchases), "Purchases")
        tabs.addTab(self._wrap(self.tbl_sales), "Sales")
        tabs.addTab(self._wrap(self.tbl_payments), "Payments")
        layout.addWidget(tabs, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # balance
    # ------------------------------------------------------------------
    def _balance_box(self) -> QGroupBox:
        box = QGroupBox("Outstanding")
        row = QHBoxLayout(box)

        def card(caption, color):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(10, 8, 10, 8)
            cap = QLabel(caption)
            cap.setStyleSheet(f"color:{GREY}; font-size:11px;")
            val = QLabel(money(0))
            val.setStyleSheet(f"color:{color}; font-size:20px; font-weight:800;")
            v.addWidget(cap)
            v.addWidget(val)
            row.addWidget(w)
            return val

        self.lbl_payable = card("We owe this supplier (Payable)", RED)
        self.lbl_paid = card("Already paid", GREEN)
        self.lbl_receivable = card("They owe us (Receivable)", RED)
        return box

    # ------------------------------------------------------------------
    # load
    # ------------------------------------------------------------------
    def refresh(self):
        try:
            bal = PartyBalanceService.compute(
                self.supplier_party_id, self.session_user_id, self.role)
        except Exception as e:
            QMessageBox.critical(self, "Supplier", f"Could not load balance:\n{e}")
            return

        payable = paid = receivable = 0.0
        for ledger in ("crops", "fertilizers"):
            d = bal.get(ledger, {})
            payable += float(d.get("payable") or 0)
            paid += float(d.get("paid") or 0)
            receivable += float(d.get("receivable") or 0)

        self.lbl_payable.setText(money(payable))
        self.lbl_paid.setText(money(paid))
        self.lbl_receivable.setText(money(receivable))

        self._fill_activity()
        self._fill_purchases()
        self._fill_sales()
        self._fill_payments()

    def _fill_activity(self):
        rows = SupplierProfileService.activity(self.supplier_party_id)
        self._activity_rows = rows
        kind_labels = {"purchase": "Purchase", "payment_out": "Paid Out",
                       "sale": "Sale", "payment_in": "Received"}
        kind_colors = {"purchase": Qt.darkBlue, "payment_out": Qt.darkRed,
                       "sale": Qt.darkGreen, "payment_in": Qt.darkGreen}
        self.tbl_activity.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = r["when"].strftime("%Y-%m-%d %H:%M") if r["when"] else ""
            kind = kind_labels.get(r["kind"], r["kind"])
            cells = [when, kind, r["reference"], r["detail"],
                     money(r["amount"]), r["status"]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                it.setForeground(kind_colors.get(r["kind"], Qt.black))
                self.tbl_activity.setItem(i, c, it)

    def _fill_purchases(self):
        rows = SupplierProfileService.purchases(self.supplier_party_id)
        self.tbl_purchases.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = r["when"].strftime("%Y-%m-%d %H:%M") if r["when"] else ""
            for_sale = f"Sale #{r['linked_sale_id']}" if r["linked_sale_id"] else "-"
            cells = [when, r["purchase_no"], r["type"], for_sale,
                     money(r["total_amount"]), r["status"]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                if c == 2 and r["type"] == "Quick":
                    it.setForeground(Qt.darkBlue)
                self.tbl_purchases.setItem(i, c, it)

    def _fill_sales(self):
        rows = SupplierProfileService.sales(self.supplier_party_id)
        self.tbl_sales.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = r["when"].strftime("%Y-%m-%d %H:%M") if r["when"] else ""
            cells = [when, r["sale_no"], money(r["total_amount"]), r["status"]]
            for c, text in enumerate(cells):
                self.tbl_sales.setItem(i, c, QTableWidgetItem(str(text)))

    def _fill_payments(self):
        rows = SupplierProfileService.payments(self.supplier_party_id)
        self.tbl_payments.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = r["when"].strftime("%Y-%m-%d %H:%M") if r["when"] else ""
            is_out = (r["direction"] == "out")
            direction = "Paid Out" if is_out else "Received"
            cells = [when, direction, r["reference"], money(r["amount"]), r["method"]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                it.setForeground(Qt.darkRed if is_out else Qt.darkGreen)
                self.tbl_payments.setItem(i, c, it)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _open_pay_supplier(self):
        from database.session import get_session
        from database.models.party import Party
        from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import (
            PurchasePaymentDialog,
        )
        with get_session() as s:
            party = s.get(Party, self.supplier_party_id)
        if party is None:
            QMessageBox.warning(self, "Supplier", "Party record not found.")
            return
        dlg = PurchasePaymentDialog(party=party, user_id=self.session_user_id,
                                    parent=self, print_slip=True)
        dlg.exec_()
        self.refresh()

    def _open_collect_payment(self):
        from database.session import get_session
        from database.models.party import Party
        from apps.product_management_app.features.profiling.dialogs.sale_payment_dialog import (
            SalePaymentDialog,
        )
        with get_session() as s:
            party = s.get(Party, self.supplier_party_id)
        if party is None:
            QMessageBox.warning(self, "Supplier", "Party record not found.")
            return
        dlg = SalePaymentDialog(party=party, user_id=self.session_user_id,
                                parent=self, print_slip=True)
        dlg.exec_()
        self.refresh()

    def _open_new_sale(self):
        hdr = SupplierProfileService.header(self.supplier_party_id)
        dlg = SupplierNewSaleDialog(self.supplier_party_id, hdr["name"], parent=self)
        dlg.exec_()
        self.refresh()

    def _open_bulk_purchase_menu(self):
        menu = QMenu(self)
        act_fert = menu.addAction("Fertilizer")
        act_crop = menu.addAction("Crops")
        chosen = menu.exec_(self.btn_bulk.mapToGlobal(self.btn_bulk.rect().bottomLeft()))
        if chosen is act_fert:
            self._open_bulk_purchase("fertilizer")
        elif chosen is act_crop:
            self._open_bulk_purchase("crops")

    def _open_bulk_purchase(self, kind: str):
        # Pre-bind this supplier via ctx["source_party_id"] - skips the whole
        # "who are you buying from" step, since we're already IN their
        # profile. Reuses the SAME dialog + PurchaseService.record() path the
        # main Cashier Purchase screen already uses; no new writing logic.
        ctx = {"source_party_id": self.supplier_party_id}
        if kind == "fertilizer":
            from apps.product_management_app.features.cashier.dialogs.intake_fertilizer_dialog import (
                FertilizerIntakeDialog,
            )
            dlg = FertilizerIntakeDialog(parent=self, ctx=ctx)
        else:
            from apps.product_management_app.features.cashier.dialogs.intake_crops_dialog import (
                CropsIntakeDialog,
            )
            dlg = CropsIntakeDialog(parent=self, ctx=ctx)
        dlg.exec_()
        self.refresh()

    def _open_activity_detail(self, row: int, _col: int):
        if row < 0 or row >= len(self._activity_rows):
            return
        r = self._activity_rows[row]
        if r["kind"] == "purchase":
            SupplierPurchaseDetailDialog(r["id"], parent=self).exec_()
        elif r["kind"] == "sale":
            SupplierSaleDetailDialog(r["id"], parent=self).exec_()
        else:  # payment_out or payment_in
            SupplierPaymentDetailDialog(r["id"], parent=self).exec_()
        self.refresh()

    def _open_purchase_detail(self, row: int, _col: int):
        rows = SupplierProfileService.purchases(self.supplier_party_id)
        if row < 0 or row >= len(rows):
            return
        SupplierPurchaseDetailDialog(rows[row]["id"], parent=self).exec_()
        self.refresh()

    def _open_sale_detail(self, row: int, _col: int):
        rows = SupplierProfileService.sales(self.supplier_party_id)
        if row < 0 or row >= len(rows):
            return
        SupplierSaleDetailDialog(rows[row]["id"], parent=self).exec_()
        self.refresh()

    def _open_payment_detail(self, row: int, _col: int):
        rows = SupplierProfileService.payments(self.supplier_party_id)
        if row < 0 or row >= len(rows):
            return
        SupplierPaymentDetailDialog(rows[row]["id"], parent=self).exec_()
        self.refresh()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _make_table(self, headers) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return t

    def _wrap(self, table) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(table)
        return w