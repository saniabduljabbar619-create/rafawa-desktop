# apps/product_management_app/features/profiling/views/records_tabs.py
"""The record-by-record tabs of a customer profile: Sales/Purchases, Products, Transaction history."""
from __future__ import annotations

from typing import List

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
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
    """One side of the Sales/Purchases tab: every sale to him or purchase from him, one row each, with a tick box
    on every row (whatever its payment status - the Flag column says what that means for a swap)."""

    selection_changed = Signal()
    changed = Signal()                      # a price was edited: books and balances moved

    def __init__(self, party_id: int, kind: str):
        super().__init__()
        self.party_id, self.kind = int(party_id), kind
        self.rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("PURCHASES from him  -  we owe him" if kind == "purchase"
                            else "SALES to him  -  he owes us")
        self.title.setStyleSheet(f"font-weight:800; font-size:13px; color:{'#b91c1c' if kind == 'purchase' else '#047857'};")
        lay.addWidget(self.title)
        bar = QHBoxLayout()
        self.btn_all = QPushButton("Tick all")
        self.btn_unpaid = QPushButton("Tick unpaid")
        self.btn_clear = QPushButton("Clear")
        self.chk_voided = QCheckBox("Show voided")
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#374151; font-weight:600;")
        self.btn_edit = QPushButton("Edit prices")
        self.btn_edit.setToolTip("Change the price of the products on the highlighted purchase (or right-click the row)")
        self.btn_edit.setVisible(kind == "purchase")
        self.btn_print = QPushButton("Print Receipt")
        self.btn_print.setToolTip("Prints the receipt of the highlighted row - nothing prints by itself")
        for b in (self.btn_all, self.btn_unpaid, self.btn_clear, self.btn_edit, self.btn_print):
            bar.addWidget(b)
        bar.addWidget(self.lbl)
        bar.addStretch(1)
        bar.addWidget(self.chk_voided)
        lay.addLayout(bar)
        word = "Sale" if kind == "sale" else "Purchase"
        self.table = _table(["", f"{word}", "Date", "Products", "Total", "Paid / set off", "Balance", "Status", "Flag"])
        self.table.setColumnWidth(0, 34)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.table.itemChanged.connect(self._ticked)
        if kind == "purchase":
            self.table.setContextMenuPolicy(Qt.CustomContextMenu)
            self.table.customContextMenuRequested.connect(self._row_menu)
        self.btn_all.clicked.connect(lambda: self._tick_all(True))
        self.btn_unpaid.clicked.connect(self.tick_unpaid)
        self.btn_clear.clicked.connect(lambda: self._tick_all(False))
        self.btn_print.clicked.connect(self.print_selected)
        self.btn_edit.clicked.connect(lambda: self.edit_prices())
        self.chk_voided.toggled.connect(lambda _on: self.reload())
        self.reload()

    def _row_menu(self, pos) -> None:
        """Right-click a purchase: edit its prices, or print its receipt."""
        row = self.table.rowAt(pos.y())
        if not 0 <= row < len(self.rows):
            return
        self.table.setCurrentCell(row, 1)
        menu = QMenu(self)
        d = self.rows[row]
        edit = menu.addAction("Edit prices…")
        edit.setEnabled(not d["voided"])
        menu.addAction("Print receipt").triggered.connect(self.print_selected)
        edit.triggered.connect(lambda: self.edit_prices(row))
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def edit_prices(self, row: int = -1) -> bool:
        """Open the price editor for the purchase on `row` (default: the highlighted one). True if it was saved."""
        from PySide2.QtWidgets import QMessageBox
        from shared.auth.session import get_session as get_login_session
        from apps.product_management_app.features.profiling.dialogs.purchase_price_edit_dialog import (
            PurchasePriceEditDialog,
        )
        r = self.table.currentRow() if row < 0 else row
        if not 0 <= r < len(self.rows):
            QMessageBox.information(self, "Edit prices", "Click a purchase row first.")
            return False
        d = self.rows[r]
        uid = int(getattr(get_login_session(), "user_id", 0) or 0)
        dlg = PurchasePriceEditDialog(self, purchase_id=int(d["id"]), ref=d["ref"], user_id=uid)
        if dlg.exec_() != dlg.Accepted or not dlg.saved:
            return False
        self.reload()
        self.changed.emit()
        return True

    def print_selected(self) -> None:
        """The full receipt (then, if wanted, a delivery note) of the highlighted sale / purchase."""
        from PySide2.QtWidgets import QMessageBox
        r = self.table.currentRow()
        if not 0 <= r < len(self.rows):
            QMessageBox.information(self, "Print Receipt", f"Click a {self.kind} row first.")
            return
        try:
            from apps.product_management_app.features.receipts_v2 import (
                PURCHASE, SALE, print_full_then_optional_delivery)
            print_full_then_optional_delivery(self, SALE if self.kind == "sale" else PURCHASE, int(self.rows[r]["id"]))
        except Exception as e:
            QMessageBox.warning(self, "Print Receipt", str(e))

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
            box.setToolTip(d["flag"])
            box.setData(Qt.UserRole, d["id"])
            t.setItem(i, 0, box)
            t.setItem(i, 1, _item(d["ref"], bold=True))
            t.setItem(i, 2, _item(_when(d["date"])))
            t.setItem(i, 3, _item(d["summary"]))
            t.setItem(i, 4, _item(_money(d["total"]), RIGHT, bold=True))
            t.setItem(i, 5, _item(_money(d["paid"]), RIGHT))
            t.setItem(i, 6, _item(_money(d["balance"]), RIGHT, "#c83c3c" if d["balance"] > 0.005 else None))
            t.setItem(i, 7, _item(d["status"].capitalize(), color=STATUS_TONE.get(d["status"])))
            flag = _item(("⚠ " + d["flag"]) if d["flag"] else "", color="#b8792e")
            flag.setToolTip(d["flag"])
            t.setItem(i, 8, flag)
        t.resizeColumnToContents(1)
        t.resizeColumnToContents(2)
        t.resizeColumnToContents(8)
        t.blockSignals(False)
        self._ticked()

    def ticked_ids(self) -> List[int]:
        out = []
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append(int(it.data(Qt.UserRole)))
        return out

    def ticked_rows(self) -> List[dict]:
        ids = set(self.ticked_ids())
        return [d for d in self.rows if d["id"] in ids]

    def _tick_all(self, on: bool, only_unpaid: bool = False) -> None:
        self.table.blockSignals(True)
        for i, d in enumerate(self.rows):
            it = self.table.item(i, 0)
            if it is not None and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if on and (not only_unpaid or d["balance"] > 0.005) else Qt.Unchecked)
        self.table.blockSignals(False)
        self._ticked()

    def tick_unpaid(self) -> None:
        self._tick_all(True, only_unpaid=True)

    def _ticked(self, *_a) -> None:
        rows = self.ticked_rows()
        bal = sum(d["balance"] for d in rows)
        self.lbl.setText(f"{len(rows)} ticked  -  balance {_money(bal)}" if rows else "Tick rows to swap them")
        self.selection_changed.emit()


class SalesPurchasesTab(QWidget):
    """Purchases on the left, Sales on the right, a line between them: tick from either side (or both) and
    swap straight from here. Any recorded document can be ticked; the flags say what that means."""

    swap_requested = Signal()
    changed = Signal()

    def __init__(self, party_id: int):
        super().__init__()
        self.party_id = int(party_id)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.lbl_sum = QLabel("")
        self.lbl_sum.setStyleSheet("font-weight:700; color:#111827;")
        self.btn_swap = QPushButton("Swap with ticked  →")
        self.btn_swap.setObjectName("PrimaryButton")
        bar.addWidget(self.lbl_sum)
        bar.addStretch(1)
        bar.addWidget(self.btn_swap)
        lay.addLayout(bar)
        self.lbl_flags = QLabel("")
        self.lbl_flags.setWordWrap(True)
        self.lbl_flags.setStyleSheet("color:#92400e; background:#fef3c7; border-radius:6px; padding:6px 10px;")
        self.lbl_flags.setVisible(False)
        lay.addWidget(self.lbl_flags)

        self.purchases = DocumentsTab(self.party_id, "purchase")
        self.sales = DocumentsTab(self.party_id, "sale")
        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self.purchases)
        self.split.addWidget(self.sales)
        self.split.setHandleWidth(4)
        self.split.setChildrenCollapsible(False)
        self.split.setStyleSheet("QSplitter::handle { background:#cbd5e1; }")
        lay.addWidget(self.split, 1)

        self.btn_swap.clicked.connect(self.swap_requested.emit)
        self.purchases.changed.connect(self.changed.emit)
        self.purchases.selection_changed.connect(self._summarise)
        self.sales.selection_changed.connect(self._summarise)
        self._summarise()

    def reload(self) -> None:
        self.purchases.reload()
        self.sales.reload()
        self._summarise()

    def flags(self) -> List[str]:
        """One line per ticked document that needs the operator's attention."""
        out = []
        for d in self.purchases.ticked_rows() + self.sales.ticked_rows():
            if d["flag"]:
                out.append(f"{d['ref']}: {d['flag']}")
        return out

    def _summarise(self, *_a) -> None:
        p, s = self.purchases.ticked_rows(), self.sales.ticked_rows()
        owe, gets = sum(d["balance"] for d in p), sum(d["balance"] for d in s)
        if p or s:
            self.lbl_sum.setText(f"Ticked: {len(p)} purchase(s), we owe {_money(owe)}   |   "
                                 f"{len(s)} sale(s), he owes {_money(gets)}")
        else:
            self.lbl_sum.setText("Tick purchases on the left and/or sales on the right, then swap")
        flags = self.flags()
        self.lbl_flags.setVisible(bool(flags))
        self.lbl_flags.setText("⚠ " + "     ⚠ ".join(flags) if flags else "")


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
