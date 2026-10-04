# apps/product_management_app/features/profiling/dialogs/swap_records_dialog.py
"""
The swap page of a customer profile.

Opens straight onto the workspace the ticked rows call for:

    purchases ticked  (we owe him)  ->  SALE workspace: pick the goods we give him from the shelf
    sales ticked      (he owes us)  ->  PURCHASE workspace: add the goods he brings
    both / nothing    ->  both workspaces

Goods we give him are ticked by category; each product's selling price is filled in when it has one (still
editable) and left empty for the operator to type when it has none. Both sides are totalled and finalizing
sets what he is owed against what he owes: if they match it is settled, otherwise the rest stays open on the
documents to be paid later (the result message reads the real balances).
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from database.services.customer_records_service import CustomerRecordsService
from database.services.offset_service import SwapService, _buy_total
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import money, style_table
from apps.product_management_app.features.cashier.dialogs.intake_cart_dialog import capture_intake_line

RIGHT = Qt.AlignRight | Qt.AlignVCenter
ALL = "All categories"
COLS = ["", "Product", "Category / priced per", "In stock", "Selling price", "In this swap"]
AMBER = QColor("#fde9c8")


class SwapRecordsDialog(QDialog):
    def __init__(self, parent=None, party_id: int = 0, party_name: str = "", purchase_ids=(), sale_ids=()):
        super().__init__(parent)
        self.party_id, self.party_name = int(party_id), party_name
        self.purchase_ids, self.sale_ids = [int(i) for i in purchase_ids], [int(i) for i in sale_ids]
        self.result_data: Dict[str, Any] = {}
        self._recv: List[Dict[str, Any]] = []
        self.products = SwapService.products_to_give()
        self.lines: Dict[int, Dict[str, Any]] = {}         # product id -> the line set in the sale dialog
        self.setWindowTitle("Swap")
        self.resize(1250, 720)

        docs = {d["id"]: d for d in CustomerRecordsService.purchases(self.party_id)}
        docs_s = {d["id"]: d for d in CustomerRecordsService.sales(self.party_id)}
        self.owe_docs = [docs[i] for i in self.purchase_ids if i in docs]
        self.recv_docs = [docs_s[i] for i in self.sale_ids if i in docs_s]

        # which workspace(s) the ticked rows call for
        self.show_give = bool(self.purchase_ids) or not self.sale_ids
        self.show_recv = bool(self.sale_ids) or not self.purchase_ids
        root = QVBoxLayout(self)
        word = ("Sale workspace - goods we give him" if self.show_give and not self.show_recv else
                "Purchase workspace - goods he brings" if self.show_recv and not self.show_give else "Swap")
        self.setWindowTitle(word)
        title = QLabel(f"Swap with {party_name or 'customer'}  -  {word}")
        title.setObjectName("Title")
        root.addWidget(title)
        sub = QLabel("What he is owed is set against what he owes. If the two match it is settled; if not, the "
                     "rest stays open on the documents to be paid later.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#6b7280; font-size:12px;")
        root.addWidget(sub)

        top = QHBoxLayout()
        neither = not (self.owe_docs or self.recv_docs)
        if self.owe_docs or neither:
            top.addWidget(self._doc_box("Ticked purchases - we owe him", self.owe_docs, "balance"), 1)
        if self.recv_docs or neither:
            top.addWidget(self._doc_box("Ticked sales - he owes us", self.recv_docs, "balance"), 1)
        root.addLayout(top)

        mid = QHBoxLayout()
        root.addLayout(mid, 1)

        give = QGroupBox("Sale workspace - goods we give him  (pick from the shelf)")
        gl = QVBoxLayout(give)
        filt = QHBoxLayout()
        self.category = QComboBox()
        cats = sorted({p["category"] for p in self.products if p["category"]})
        types = sorted({p["type"].capitalize() for p in self.products})
        self.category.addItem(ALL)
        for c in types + cats:
            self.category.addItem(c)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search product")
        filt.addWidget(QLabel("Category"))
        filt.addWidget(self.category)
        filt.addWidget(self.search, 1)
        gl.addLayout(filt)
        self.tbl_give = QTableWidget(0, len(COLS))
        self.tbl_give.setHorizontalHeaderLabels(COLS)
        style_table(self.tbl_give, 32)
        self.tbl_give.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_give.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_give.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 30), (2, 230), (3, 130), (4, 110), (5, 260)):
            self.tbl_give.setColumnWidth(c, w)
        self.tbl_give.horizontalHeader().setStretchLastSection(False)
        gl.addWidget(self.tbl_give, 1)
        row = QHBoxLayout()
        self.btn_add_give = QPushButton("Add / change selected")
        self.btn_rm_give = QPushButton("Remove")
        row.addWidget(self.btn_add_give)
        row.addWidget(self.btn_rm_give)
        hint = QLabel("Tick a product (or double-click it): the same sale dialog as the checkout opens for the "
                      "quantity and price. Its selling price is filled in when it has one.")
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        gl.addLayout(row)
        mid.addWidget(give, 3)
        give.setVisible(self.show_give)

        recv = QGroupBox("Purchase workspace - goods he brings now  (new purchase)")
        rl = QVBoxLayout(recv)
        self.tbl_recv = QTableWidget(0, 4)
        self.tbl_recv.setHorizontalHeaderLabels(["Product", "Qty", "Price", "Total"])
        style_table(self.tbl_recv, 32)
        self.tbl_recv.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_recv.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_recv.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        rl.addWidget(self.tbl_recv, 1)
        row = QHBoxLayout()
        self.btn_add_recv = QPushButton("+ Add item")
        self.btn_rm_recv = QPushButton("Remove")
        row.addWidget(self.btn_add_recv)
        row.addWidget(self.btn_rm_recv)
        row.addStretch(1)
        rl.addLayout(row)
        mid.addWidget(recv, 2)
        recv.setVisible(self.show_recv)

        self.lbl_owe = QLabel("")
        self.lbl_recv = QLabel("")
        self.lbl_result = QLabel("")
        for w in (self.lbl_owe, self.lbl_recv):
            w.setStyleSheet("font-weight:600; color:#374151;")
        self.lbl_result.setStyleSheet("font-size:16px; font-weight:800;")
        tot = QHBoxLayout()
        tot.addWidget(self.lbl_owe)
        tot.addSpacing(20)
        tot.addWidget(self.lbl_recv)
        tot.addStretch(1)
        tot.addWidget(self.lbl_result)
        root.addLayout(tot)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Note (optional), e.g. 'maize for NPK'")
        root.addWidget(self.note)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Finalize swap")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.setMinimumHeight(38)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_save)
        root.addLayout(foot)

        self.category.currentIndexChanged.connect(self._render_products)
        self.search.textChanged.connect(self._render_products)
        self.tbl_give.itemChanged.connect(self._ticked)
        self.tbl_give.cellDoubleClicked.connect(lambda r, _c: self._open_line(self._pid_at(r)))
        self.btn_add_give.clicked.connect(lambda: self._open_line(self._pid_at(self.tbl_give.currentRow())))
        self.btn_rm_give.clicked.connect(self._remove_give)
        self.btn_add_recv.clicked.connect(self._add_recv)
        self.btn_rm_recv.clicked.connect(self._remove_recv)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)
        self._render_products()
        self._summarise()

    # ------------------------------------------------------------------ pieces
    def _doc_box(self, title, docs, key) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        t = QTableWidget(len(docs), 4)
        t.setHorizontalHeaderLabels(["Reference", "Products", "Counts (still unpaid)", "Flag"])
        style_table(t, 28)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setColumnWidth(2, 150)
        t.setColumnWidth(3, 280)
        t.setMaximumHeight(130)
        for i, d in enumerate(docs):
            t.setItem(i, 0, QTableWidgetItem(d["ref"]))
            t.setItem(i, 1, QTableWidgetItem(d["summary"]))
            it = QTableWidgetItem(money(d[key]))
            it.setTextAlignment(RIGHT)
            t.setItem(i, 2, it)
            flag = QTableWidgetItem(("⚠ " + d["flag"]) if d.get("flag") else "")
            flag.setForeground(QColor("#b8792e"))
            t.setItem(i, 3, flag)
        lay.addWidget(t)
        if not docs:
            hint = QLabel("None ticked - the goods below make up this side.")
            hint.setStyleSheet("color:#6b7280; font-size:11px;")
            lay.addWidget(hint)
        return box

    def _pid_at(self, row: int):
        it = self.tbl_give.item(row, 0) if row >= 0 else None
        return None if it is None else int(it.data(Qt.UserRole))

    def _render_products(self, *_a) -> None:
        cat, term = self.category.currentText(), self.search.text().strip().lower()
        shown = [p for p in self.products
                 if (cat == ALL or cat == p["category"] or cat == p["type"].capitalize())
                 and (not term or term in p["name"].lower())]
        t = self.tbl_give
        t.blockSignals(True)
        t.setRowCount(len(shown))
        for i, p in enumerate(shown):
            crops = p["type"] == "crops"
            ln = self.lines.get(p["id"])
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            box.setCheckState(Qt.Checked if ln else Qt.Unchecked)
            box.setData(Qt.UserRole, p["id"])
            t.setItem(i, 0, box)
            unit = "per kg" if crops else "per bag"
            price = QTableWidgetItem(f"{p['price']:,.2f}" if p["price"] else "not set")
            price.setTextAlignment(RIGHT)
            if not p["price"]:
                price.setForeground(QColor("#b8792e"))
                price.setToolTip("No selling price yet - you type it in the sale dialog")
            swap = (f"{ln['qty_bags']:g} bags" + (f" / {ln['total_weight_kg']:,.0f} kg" if crops else "")
                    + f"  =  {money(ln['line_total'])}") if ln else ""
            for c, text in ((1, p["name"]), (2, f"{p['category'] or p['type'].capitalize()}  ·  {unit}"),
                            (3, f"{p['bags']:g} bags" + (f" / {p['kg']:,.0f} kg" if crops else "")),
                            (5, swap)):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if ln and c == 5:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                t.setItem(i, c, it)
            price.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            t.setItem(i, 4, price)
        t.blockSignals(False)

    def _ticked(self, item) -> None:
        """Ticking a product opens the sale dialog for it; unticking takes it out of the swap."""
        if item.column() != 0:
            return
        pid = int(item.data(Qt.UserRole))
        if item.checkState() == Qt.Checked:
            if pid not in self.lines and not self._open_line(pid):
                self.tbl_give.blockSignals(True)
                item.setCheckState(Qt.Unchecked)
                self.tbl_give.blockSignals(False)
        else:
            self.lines.pop(pid, None)
            self._render_products()
            self._summarise()

    def _open_line(self, pid) -> bool:
        """The checkout's own sale-item dialog: quantity (bags / kg, or by bag), price, variant."""
        from database.services.sales_service import SaleService
        from apps.product_management_app.features.cashier.dialogs.sale_item_dialog import SaleItemDialog
        if pid is None:
            QMessageBox.information(self, "Swap", "Select a product first.")
            return False
        p = next((x for x in self.products if x["id"] == pid), None)
        if p is None:
            return False
        info = SaleService.get_product_for_sale(int(pid)) or {}
        dlg = SaleItemDialog(self, product_id=int(pid), product_name=p["name"],
                             product_type="fertilizer" if p["type"] == "fertilizer" else "crops",
                             unit_price=float(info.get("sale_rate") or p["price"] or 0.0))
        if dlg.exec_() != dlg.Accepted:
            return False
        pay = dlg.payload()
        if float(pay["unit_price"]) <= 0:
            QMessageBox.warning(self, "Swap", f"Enter a selling price for {p['name']}.")
            return False
        self.lines[int(pid)] = {
            "product_id": int(pid), "name": p["name"], "qty_bags": int(pay["qty_bags"]),
            "total_weight_kg": float(pay["total_weight_kg"]), "unit_price": float(pay["unit_price"]),
            "line_total": round(float(pay["line_total"]), 2), "variant": pay.get("variant", "")}
        self._render_products()
        self._summarise()
        return True

    def _remove_give(self) -> None:
        pid = self._pid_at(self.tbl_give.currentRow())
        if pid is not None and self.lines.pop(pid, None) is not None:
            self._render_products()
            self._summarise()

    def _add_recv(self) -> None:
        kind, ok = QInputDialog.getItem(self, "Goods he brings", "Type", ["Crops", "Fertilizer"], 0, False)
        if not ok:
            return
        line = capture_intake_line(self, kind.lower())
        if line is not None:
            self._recv.append(line)
            self._render_recv()

    def _remove_recv(self) -> None:
        r = self.tbl_recv.currentRow()
        if 0 <= r < len(self._recv):
            del self._recv[r]
            self._render_recv()

    def _render_recv(self) -> None:
        t = self.tbl_recv
        t.setRowCount(len(self._recv))
        for i, ln in enumerate(self._recv):
            crops = ln["product_type"] != "fertilizer"
            qty = f"{ln['qty_bags']} bags" + (f" / {ln['total_weight_kg']:,.0f} kg" if crops else "")
            for c, text in enumerate((ln["product_name"], qty, money(ln["unit_cost"]), money(_buy_total(ln)))):
                it = QTableWidgetItem(text)
                if c:
                    it.setTextAlignment(RIGHT)
                t.setItem(i, c, it)
        self._summarise()

    # ------------------------------------------------------------------ the sums
    def given_lines(self) -> List[Dict[str, Any]]:
        return [{"product_id": ln["product_id"], "qty_bags": ln["qty_bags"],
                 "total_weight_kg": ln["total_weight_kg"], "unit_price": ln["unit_price"]}
                for ln in self.lines.values()]

    def totals(self):
        """(we owe him, he owes us) over the ticked records plus the new goods on each side."""
        owe = sum(d["balance"] for d in self.owe_docs) + sum(_buy_total(i) for i in self._recv)
        gets = sum(d["balance"] for d in self.recv_docs) + sum(ln["line_total"] for ln in self.lines.values())
        return round(owe, 2), round(gets, 2)

    def _summarise(self) -> None:
        owe, gets = self.totals()
        self.lbl_owe.setText(f"We owe him: {money(owe)}")
        self.lbl_recv.setText(f"He owes us: {money(gets)}")
        diff = round(owe - gets, 2)
        if owe <= 0 or gets <= 0:
            self.lbl_result.setText("")
        elif diff > 0:
            self.lbl_result.setText(f"After the swap we still owe him {money(diff)}")
            self.lbl_result.setStyleSheet("font-size:16px; font-weight:800; color:#b91c1c;")
        elif diff < 0:
            self.lbl_result.setText(f"After the swap he still owes us {money(-diff)}")
            self.lbl_result.setStyleSheet("font-size:16px; font-weight:800; color:#047857;")
        else:
            self.lbl_result.setText("Even - nothing left to pay")
            self.lbl_result.setStyleSheet("font-size:16px; font-weight:800; color:#374151;")

    def _save(self) -> None:
        given = self.given_lines()
        owe, gets = self.totals()
        if owe <= 0 or gets <= 0:
            QMessageBox.warning(self, "Swap", "Both sides need something: tick purchases or add the goods he brings, "
                                              "and tick sales or pick the goods we give him.")
            return
        self.btn_save.setEnabled(False)                 # one attempt at a time: no double recording
        try:
            self.result_data = SwapService.record_from_records(
                self.party_id, self.purchase_ids, self.sale_ids, list(self._recv), given, self.note.text())
        except PermissionDenied as e:
            self.btn_save.setEnabled(True)
            QMessageBox.warning(self, "Not allowed", str(e))
            return
        except Exception as e:
            self.btn_save.setEnabled(True)
            QMessageBox.warning(self, "Swap NOT recorded",
                                f"{e}\n\nNothing was saved - you can correct it and finalize again.")
            return
        r = self.result_data
        if r["we_owe_him"] > 0.005:
            left = f"Settled as far as it goes. We still owe him {money(r['we_owe_him'])} - pay it later as usual."
        elif r["he_owes_us"] > 0.005:
            left = f"Settled as far as it goes. He still owes us {money(r['he_owes_us'])} - collect it later as usual."
        else:
            left = "Fully settled - nothing left to pay either way."
        QMessageBox.information(self, "Swap recorded", f"{money(r['offset'])} set off.\n{left}")
        self.accept()
