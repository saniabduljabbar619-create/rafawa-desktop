# apps/product_management_app/features/suppliers/views/supplier_activity_dialogs.py
# -*- coding: utf-8 -*-
"""
Detail dialogs for a supplier's Activity/Purchases/Payments rows.

Read-only and informational, as requested: double-clicking any row anywhere
in the supplier profile opens ONE of these two, showing the full history of
what actually happened - line items, who did it, and every payment tied to
it - rather than the summary row the tables show. No money logic lives here;
these only display what SupplierProfileService already computed.
"""
from __future__ import annotations

from typing import Dict, Any

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QFrame
)

from database.services.supplier_profile_service import SupplierProfileService

GREY = "#6b7280"
INK = "#111827"
RED = "#c0392b"
GREEN = "#27ae60"


def money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


def _when(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


def _field_row(layout: QVBoxLayout, label: str, value: str, color: str = INK):
    row = QHBoxLayout()
    cap = QLabel(label)
    cap.setStyleSheet(f"color:{GREY}; font-size:11px;")
    cap.setFixedWidth(120)
    val = QLabel(str(value))
    val.setStyleSheet(f"color:{color}; font-size:13px; font-weight:600;")
    val.setWordWrap(True)
    row.addWidget(cap)
    row.addWidget(val, 1)
    layout.addLayout(row)


class SupplierPurchaseDetailDialog(QDialog):
    """Full detail for one purchase: header, line items, payments against it."""

    def __init__(self, purchase_id: int, parent=None):
        super().__init__(parent)
        self.purchase_id = int(purchase_id)
        d = SupplierProfileService.purchase_detail(self.purchase_id)

        self.setWindowTitle(f"Purchase Detail - {d.get('purchase_no', '')}")
        self.setMinimumWidth(680)
        self.setMinimumHeight(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        if not d:
            layout.addWidget(QLabel("This purchase could not be found."))
            btn = QPushButton("Close")
            btn.clicked.connect(self.reject)
            layout.addWidget(btn)
            return

        title = QLabel(d["purchase_no"])
        title.setStyleSheet("font-size:16px; font-weight:800;")
        layout.addWidget(title)

        _field_row(layout, "Type", d["type"])
        _field_row(layout, "Status", d["status"])
        _field_row(layout, "Created by", d["created_by"] or "-")
        _field_row(layout, "Created at", _when(d["created_at"]))
        if d.get("linked_sale_id"):
            _field_row(layout, "For sale", f"Sale #{d['linked_sale_id']}")
        if d.get("notes"):
            _field_row(layout, "Notes", d["notes"])

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)

        layout.addWidget(QLabel("Line items"))
        items_tbl = QTableWidget(0, 4)
        items_tbl.setHorizontalHeaderLabels(
            ["Product", "Qty / Weight", "Rate", "Line Total"])
        items_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        items_tbl.verticalHeader().setVisible(False)
        items_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lines = d.get("lines", [])
        items_tbl.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            qty_txt = (f"{ln['total_weight_kg']:g} kg" if ln.get("total_weight_kg")
                      else f"{ln['qty_bags']:g} bag(s)")
            for c, text in enumerate([ln["product"], qty_txt,
                                      money(ln["unit_cost_rate"]),
                                      money(ln["line_total"])]):
                items_tbl.setItem(i, c, QTableWidgetItem(str(text)))
        items_tbl.setMaximumHeight(140)
        layout.addWidget(items_tbl)

        totals = QHBoxLayout()
        for cap, val, color in [
            ("Total", d["total_amount"], INK),
            ("Paid", d["paid_total"], GREEN),
            ("Balance", d["balance"], RED if d["balance"] > 0.005 else GREEN),
        ]:
            w = QLabel(f"{cap}: {money(val)}")
            w.setStyleSheet(f"color:{color}; font-weight:700;")
            totals.addWidget(w)
        totals.addStretch(1)
        layout.addLayout(totals)

        layout.addWidget(QLabel("Payments against this purchase"))
        pay_tbl = QTableWidget(0, 4)
        pay_tbl.setHorizontalHeaderLabels(["When", "Amount", "Method", "Notes"])
        pay_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        pay_tbl.verticalHeader().setVisible(False)
        pay_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        pays = d.get("payments", [])
        pay_tbl.setRowCount(len(pays))
        for i, p in enumerate(pays):
            for c, text in enumerate([_when(p["when"]), money(p["amount"]),
                                      p["method"], p["notes"]]):
                pay_tbl.setItem(i, c, QTableWidgetItem(str(text)))
        layout.addWidget(pay_tbl, 1)

        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class SupplierSaleDetailDialog(QDialog):
    """Full detail for one sale made TO this party: header, line items,
    and every payment received against it. Mirrors the purchase detail
    dialog on the opposite side of the ledger."""

    def __init__(self, sale_id: int, parent=None):
        super().__init__(parent)
        self.sale_id = int(sale_id)
        d = SupplierProfileService.sale_detail(self.sale_id)

        self.setWindowTitle(f"Sale Detail - {d.get('sale_no', '')}")
        self.setMinimumWidth(680)
        self.setMinimumHeight(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        if not d:
            layout.addWidget(QLabel("This sale could not be found."))
            btn = QPushButton("Close")
            btn.clicked.connect(self.reject)
            layout.addWidget(btn)
            return

        title = QLabel(d["sale_no"])
        title.setStyleSheet("font-size:16px; font-weight:800;")
        layout.addWidget(title)

        _field_row(layout, "Status", d["status"])
        _field_row(layout, "Created by", d["created_by"] or "-")
        _field_row(layout, "Created at", _when(d["created_at"]))
        if d.get("discount"):
            _field_row(layout, "Discount", money(d["discount"]))
        if d.get("notes"):
            _field_row(layout, "Notes", d["notes"])

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)

        layout.addWidget(QLabel("Line items"))
        items_tbl = QTableWidget(0, 4)
        items_tbl.setHorizontalHeaderLabels(
            ["Product", "Qty / Weight", "Rate", "Line Total"])
        items_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        items_tbl.verticalHeader().setVisible(False)
        items_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lines = d.get("lines", [])
        items_tbl.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            qty_txt = (f"{ln['total_weight_kg']:g} kg" if ln.get("total_weight_kg")
                      else f"{ln['qty_bags']:g} bag(s)")
            for c, text in enumerate([ln["product"], qty_txt,
                                      money(ln["unit_sale_rate"]),
                                      money(ln["line_total"])]):
                items_tbl.setItem(i, c, QTableWidgetItem(str(text)))
        items_tbl.setMaximumHeight(140)
        layout.addWidget(items_tbl)

        totals = QHBoxLayout()
        for cap, val, color in [
            ("Total", d["total_amount"], INK),
            ("Received", d["received_total"], GREEN),
            ("Balance", d["balance"], RED if d["balance"] > 0.005 else GREEN),
        ]:
            w = QLabel(f"{cap}: {money(val)}")
            w.setStyleSheet(f"color:{color}; font-weight:700;")
            totals.addWidget(w)
        totals.addStretch(1)
        layout.addLayout(totals)

        layout.addWidget(QLabel("Payments received for this sale"))
        pay_tbl = QTableWidget(0, 4)
        pay_tbl.setHorizontalHeaderLabels(["When", "Amount", "Method", "Notes"])
        pay_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        pay_tbl.verticalHeader().setVisible(False)
        pay_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        pays = d.get("payments", [])
        pay_tbl.setRowCount(len(pays))
        for i, p in enumerate(pays):
            for c, text in enumerate([_when(p["when"]), money(p["amount"]),
                                      p["method"], p["notes"]]):
                pay_tbl.setItem(i, c, QTableWidgetItem(str(text)))
        layout.addWidget(pay_tbl, 1)

        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class SupplierPaymentDetailDialog(QDialog):
    """Full detail for one payment: amount, method, who recorded it, and
    which purchase it paid down - with a way to jump to that purchase."""

    def __init__(self, payment_id: int, parent=None):
        super().__init__(parent)
        self.payment_id = int(payment_id)
        d = SupplierProfileService.payment_detail(self.payment_id)

        self.setWindowTitle("Payment Detail")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        if not d:
            layout.addWidget(QLabel("This payment could not be found."))
            btn = QPushButton("Close")
            btn.clicked.connect(self.reject)
            layout.addWidget(btn)
            return

        title = QLabel(money(d["amount"]))
        title.setStyleSheet(f"color:{GREEN}; font-size:22px; font-weight:900;")
        layout.addWidget(title)

        is_out = (d.get("direction") == "out")

        _field_row(layout, "Method", d["method"] or "-")
        _field_row(layout, "Recorded by", d["recorded_by"] or "-")
        _field_row(layout, "When", _when(d["when"]))
        if d.get("notes"):
            _field_row(layout, "Notes", d["notes"])

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)

        if is_out:
            _field_row(layout, "Paid towards", d["purchase_no"] or "-")
            _field_row(layout, "Purchase total", money(d["purchase_total"]))
        else:
            _field_row(layout, "Received for", d["sale_no"] or "-")
            _field_row(layout, "Sale total", money(d["sale_total"]))

        btns = QHBoxLayout()
        if d.get("purchase_id"):
            self.btn_view_purchase = QPushButton("View Purchase")
            self.btn_view_purchase.clicked.connect(self._open_purchase)
            btns.addWidget(self.btn_view_purchase)
        elif d.get("sale_id"):
            self.btn_view_sale = QPushButton("View Sale")
            self.btn_view_sale.clicked.connect(self._open_sale)
            btns.addWidget(self.btn_view_sale)
        btns.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

        self._purchase_id = d.get("purchase_id")
        self._sale_id = d.get("sale_id")

    def _open_purchase(self):
        if not self._purchase_id:
            return
        self.accept()
        dlg = SupplierPurchaseDetailDialog(self._purchase_id, parent=self.parent())
        dlg.exec_()

    def _open_sale(self):
        if not self._sale_id:
            return
        self.accept()
        dlg = SupplierSaleDetailDialog(self._sale_id, parent=self.parent())
        dlg.exec_()