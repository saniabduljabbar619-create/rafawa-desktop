# apps/product_management_app/features/profiling/views/records_tabs.py
"""The record-by-record tabs of a customer profile: Sales, Purchases, Products, Transaction history."""
from __future__ import annotations

from typing import List

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.customer_records_service import CustomerRecordsService as R

RIGHT = Qt.AlignRight | Qt.AlignVCenter
STATUS_TONE = {"paid": "#1e8c46", "partial": "#c88c14", "unpaid": "#c83c3c", "voided": "#9ca3af"}


def _money(v: float) -> str:
    return f"{float(v or 0):,.2f}"


def _when(d) -> str:
    return d.strftime("%d/%m/%Y %H:%M") if d else ""


def _table(headers: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.horizontalHeader().setStretchLastSection(True)
    t.setAlternatingRowColors(True)
    return t


def _item(text, align=None, color=None, bold=False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    if align is not None:
        it.setTextAlignment(align)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font()
        f.setBold(True)
        it.setFont(f)
    return it


class DocumentsTab(QWidget):
    """Every sale to him / purchase from him, one row each, with a tick box to select several."""

    swap_requested = Signal()
    selection_changed = Signal()

    def __init__(self, party_id: int, kind: str):
        super().__init__()
        self.party_id, self.kind = int(party_id), kind
        self.rows = []
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_unpaid = QPushButton("Tick all unpaid")
        self.btn_clear = QPushButton("Clear ticks")
        self.chk_voided = QCheckBox("Show voided")
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#374151; font-weight:600;")
        self.btn_swap = QPushButton("Swap with ticked  →")
        self.btn_swap.setObjectName("PrimaryButton")
        for b in (self.btn_unpaid, self.btn_clear):
            bar.addWidget(b)
        bar.addWidget(self.lbl)
        bar.addStretch(1)
        bar.addWidget(self.chk_voided)
        bar.addWidget(self.btn_swap)
        lay.addLayout(bar)
        word = "Sale" if kind == "sale" else "Purchase"
        self.table = _table(["", f"{word}", "Date", "Products", "Total", "Paid / set off", "Balance", "Status"])
        self.table.setColumnWidth(0, 34)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.table.itemChanged.connect(self._ticked)
        self.btn_unpaid.clicked.connect(lambda: self._tick_all(True))
        self.btn_clear.clicked.connect(lambda: self._tick_all(False))
        self.chk_voided.toggled.connect(lambda _on: self.reload())
        self.btn_swap.clicked.connect(self.swap_requested.emit)
        self.reload()

    def reload(self) -> None:
        keep = set(self.ticked_ids())
        self.rows = [d for d in R.documents(self.party_id, self.kind) if self.chk_voided.isChecked() or not d["voided"]]
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(self.rows))
        for i, d in enumerate(self.rows):
            box = QTableWidgetItem()
            if d["selectable"]:
                box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                box.setCheckState(Qt.Checked if d["id"] in keep else Qt.Unchecked)
            else:
                box.setFlags(Qt.NoItemFlags)
                box.setToolTip("Voided" if d["voided"] else "Fully settled - nothing left to swap against")
            box.setData(Qt.UserRole, d["id"])
            t.setItem(i, 0, box)
            t.setItem(i, 1, _item(d["ref"], bold=True))
            t.setItem(i, 2, _item(_when(d["date"])))
            t.setItem(i, 3, _item(d["summary"]))
            t.setItem(i, 4, _item(_money(d["total"]), RIGHT, bold=True))
            t.setItem(i, 5, _item(_money(d["paid"]), RIGHT))
            t.setItem(i, 6, _item(_money(d["balance"]), RIGHT, "#c83c3c" if d["balance"] > 0.005 else None))
            t.setItem(i, 7, _item(d["status"].capitalize(), color=STATUS_TONE.get(d["status"])))
        t.resizeColumnToContents(1)
        t.resizeColumnToContents(2)
        t.blockSignals(False)
        self._ticked()

    def ticked_ids(self) -> List[int]:
        out = []
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append(int(it.data(Qt.UserRole)))
        return out

    def _tick_all(self, on: bool) -> None:
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self._ticked()

    def _ticked(self, *_a) -> None:
        ids = set(self.ticked_ids())
        bal = sum(d["balance"] for d in self.rows if d["id"] in ids)
        self.lbl.setText(f"{len(ids)} ticked  -  balance {_money(bal)}" if ids else "Tick rows to swap them")
        self.selection_changed.emit()


class ProductsTab(QWidget):
    """Per product: what we bought from him and what we sold him."""

    def __init__(self, party_id: int):
        super().__init__()
        self.party_id = int(party_id)
        lay = QVBoxLayout(self)
        self.table = _table(["Product", "Type", "Bought from him", "Value bought", "Sold to him", "Value sold",
                             "Difference"])
        lay.addWidget(self.table)
        self.reload()

    @staticmethod
    def _qty(bags, kg, crops) -> str:
        return f"{bags:g} bags" + (f" / {kg:,.0f} kg" if crops and kg else "") if bags or kg else "-"

    def reload(self) -> None:
        rows = R.products(self.party_id)
        t = self.table
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            crops = r["type"] == "crops"
            t.setItem(i, 0, _item(r["name"], bold=True))
            t.setItem(i, 1, _item(r["type"].capitalize()))
            t.setItem(i, 2, _item(self._qty(r["bought_bags"], r["bought_kg"], crops), RIGHT))
            t.setItem(i, 3, _item(_money(r["bought_value"]), RIGHT, "#c83c3c"))
            t.setItem(i, 4, _item(self._qty(r["sold_bags"], r["sold_kg"], crops), RIGHT))
            t.setItem(i, 5, _item(_money(r["sold_value"]), RIGHT, "#1e8c46"))
            t.setItem(i, 6, _item(_money(r["net"]), RIGHT, bold=True))


class HistoryTab(QWidget):
    """Everything in date order - sales, purchases, payments, returns, swaps - with the running position."""

    def __init__(self, party_id: int):
        super().__init__()
        self.party_id = int(party_id)
        lay = QVBoxLayout(self)
        self.note = QLabel("Running position: positive = he owes us, negative = we owe him.")
        self.note.setStyleSheet("color:#6b7280;")
        lay.addWidget(self.note)
        self.table = _table(["Date", "Type", "Reference", "Details", "Amount", "Position"])
        lay.addWidget(self.table, 1)
        self.reload()

    def reload(self) -> None:
        events = R.history(self.party_id)
        t = self.table
        t.setRowCount(len(events))
        for i, e in enumerate(reversed(events)):                   # newest first
            amount = e.get("amount", abs(e["delta"]))
            t.setItem(i, 0, _item(_when(e["date"])))
            t.setItem(i, 1, _item(e["type"], bold=True))
            t.setItem(i, 2, _item(e["ref"]))
            t.setItem(i, 3, _item(e["detail"]))
            t.setItem(i, 4, _item(_money(amount), RIGHT))
            t.setItem(i, 5, _item(_money(e["running"]), RIGHT, "#c83c3c" if e["running"] < -0.005 else "#1e8c46",
                                  bold=True))
