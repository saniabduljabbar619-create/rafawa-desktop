# apps/product_management_app/features/profiling/dialogs/swap_records_dialog.py
"""
The swap page of a customer profile.

Starts from the rows ticked on his Sales / Purchases tabs:

    purchases ticked  = goods he already brought us (we still owe him for them)
    sales ticked      = goods we already gave him   (he still owes us for them)

then lets the operator add NEW goods either way - the goods we give him are picked from the shelf by category
(tick several, set quantity and price), the goods he brings are added line by line - computes both sides and
finalizes: everything he is owed is set against everything he owes, only the difference is left to settle.
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide2.QtCore import Qt
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
COLS = ["", "Product", "Category", "In stock", "Bags", "Kg", "Price", "Total"]


def _num(text: str) -> float:
    try:
        return float(str(text).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


class SwapRecordsDialog(QDialog):
    def __init__(self, parent=None, party_id: int = 0, party_name: str = "", purchase_ids=(), sale_ids=()):
        super().__init__(parent)
        self.party_id, self.party_name = int(party_id), party_name
        self.purchase_ids, self.sale_ids = [int(i) for i in purchase_ids], [int(i) for i in sale_ids]
        self.result_data: Dict[str, Any] = {}
        self._recv: List[Dict[str, Any]] = []
        self.products = SwapService.products_to_give()
        self.pick: Dict[int, Dict[str, float]] = {}        # product id -> {on, bags, kg, price}
        self.setWindowTitle("Swap")
        self.resize(1250, 720)

        docs = {d["id"]: d for d in CustomerRecordsService.purchases(self.party_id)}
        docs_s = {d["id"]: d for d in CustomerRecordsService.sales(self.party_id)}
        self.owe_docs = [docs[i] for i in self.purchase_ids if i in docs]
        self.recv_docs = [docs_s[i] for i in self.sale_ids if i in docs_s]

        root = QVBoxLayout(self)
        title = QLabel(f"Swap with {party_name or 'customer'}")
        title.setObjectName("Title")
        root.addWidget(title)
        sub = QLabel("What he is owed (purchases from him, and goods he brings now) is set against what he owes "
                     "(sales to him, and goods we give him now). Only the difference is left to pay.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#6b7280; font-size:12px;")
        root.addWidget(sub)

        top = QHBoxLayout()
        top.addWidget(self._doc_box("Ticked purchases - we owe him", self.owe_docs, "balance"), 1)
        top.addWidget(self._doc_box("Ticked sales - he owes us", self.recv_docs, "balance"), 1)
        root.addLayout(top)

        mid = QHBoxLayout()
        root.addLayout(mid, 1)

        give = QGroupBox("Goods we give him  (pick from the shelf)")
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
        self.tbl_give.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_give.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 30), (2, 100), (3, 130), (4, 70), (5, 80), (6, 90), (7, 110)):
            self.tbl_give.setColumnWidth(c, w)
        self.tbl_give.horizontalHeader().setStretchLastSection(False)
        gl.addWidget(self.tbl_give, 1)
        hint = QLabel("Tick the products, then type Bags (and Kg for crops) and the Price. Crops are priced per kg, "
                      "fertilizer per bag.")
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        hint.setWordWrap(True)
        gl.addWidget(hint)
        mid.addWidget(give, 3)

        recv = QGroupBox("Goods he brings now  (new purchase)")
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
        self.tbl_give.itemChanged.connect(self._edited)
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
        t = QTableWidget(len(docs), 3)
        t.setHorizontalHeaderLabels(["Reference", "Products", "Balance"])
        style_table(t, 28)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setMaximumHeight(130)
        for i, d in enumerate(docs):
            t.setItem(i, 0, QTableWidgetItem(d["ref"]))
            t.setItem(i, 1, QTableWidgetItem(d["summary"]))
            it = QTableWidgetItem(money(d[key]))
            it.setTextAlignment(RIGHT)
            t.setItem(i, 2, it)
        lay.addWidget(t)
        if not docs:
            hint = QLabel("None ticked - the goods below make up this side.")
            hint.setStyleSheet("color:#6b7280; font-size:11px;")
            lay.addWidget(hint)
        return box

    def _state(self, p) -> Dict[str, float]:
        return self.pick.setdefault(p["id"], {"on": 0, "bags": 0, "kg": 0.0, "price": p["price"]})

    def _line_total(self, p) -> float:
        st = self._state(p)
        return round((st["kg"] if p["type"] == "crops" else st["bags"]) * st["price"], 2)

    def _render_products(self, *_a) -> None:
        cat, term = self.category.currentText(), self.search.text().strip().lower()
        shown = [p for p in self.products
                 if (cat == ALL or cat == p["category"] or cat == p["type"].capitalize())
                 and (not term or term in p["name"].lower())]
        t = self.tbl_give
        t.blockSignals(True)
        t.setRowCount(len(shown))
        for i, p in enumerate(shown):
            st = self._state(p)
            crops = p["type"] == "crops"
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            box.setCheckState(Qt.Checked if st["on"] else Qt.Unchecked)
            box.setData(Qt.UserRole, p["id"])
            t.setItem(i, 0, box)
            for c, text in ((1, p["name"]), (2, p["category"] or p["type"].capitalize()),
                            (3, f"{p['bags']:g} bags" + (f" / {p['kg']:,.0f} kg" if crops else ""))):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                t.setItem(i, c, it)
            for c, text, editable in ((4, f"{st['bags']:g}" if st["bags"] else "", True),
                                      (5, f"{st['kg']:g}" if st["kg"] else "", crops),
                                      (6, f"{st['price']:g}", True)):
                it = QTableWidgetItem(text)
                it.setTextAlignment(RIGHT)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsEditable if editable else Qt.NoItemFlags))
                t.setItem(i, c, it)
            tot = QTableWidgetItem(money(self._line_total(p)) if st["on"] else "")
            tot.setTextAlignment(RIGHT)
            tot.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            t.setItem(i, 7, tot)
        t.blockSignals(False)

    def _edited(self, item) -> None:
        box = self.tbl_give.item(item.row(), 0)
        if box is None:
            return
        p = next((x for x in self.products if x["id"] == box.data(Qt.UserRole)), None)
        if p is None:
            return
        st = self._state(p)
        c = item.column()
        if c == 0:
            st["on"] = 1 if item.checkState() == Qt.Checked else 0
        elif c == 4:
            st["bags"] = int(_num(item.text()))
        elif c == 5:
            st["kg"] = _num(item.text())
        elif c == 6:
            st["price"] = _num(item.text())
        else:
            return
        if c in (4, 5, 6) and (st["bags"] or st["kg"]):
            st["on"] = 1                                   # typing a quantity ticks the product
            self.tbl_give.blockSignals(True)
            box.setCheckState(Qt.Checked)
            self.tbl_give.blockSignals(False)
        tot = self.tbl_give.item(item.row(), 7)
        self.tbl_give.blockSignals(True)
        tot.setText(money(self._line_total(p)) if st["on"] else "")
        self.tbl_give.blockSignals(False)
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
        out = []
        for p in self.products:
            st = self.pick.get(p["id"])
            if not st or not st["on"]:
                continue
            out.append({"product_id": p["id"], "qty_bags": int(st["bags"]),
                        "total_weight_kg": st["kg"] if p["type"] == "crops" else 0.0, "unit_price": st["price"]})
        return out

    def totals(self):
        """(we owe him, he owes us) over the ticked records plus the new goods on each side."""
        owe = sum(d["balance"] for d in self.owe_docs) + sum(_buy_total(i) for i in self._recv)
        gets = sum(d["balance"] for d in self.recv_docs) + sum(
            self._line_total(p) for p in self.products if self.pick.get(p["id"], {}).get("on"))
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
        bad = [p["name"] for p in self.products if self.pick.get(p["id"], {}).get("on")
               and (self._line_total(p) <= 0)]
        if bad:
            QMessageBox.warning(self, "Swap", f"Enter the quantity and price for: {', '.join(bad)}")
            return
        owe, gets = self.totals()
        if owe <= 0 or gets <= 0:
            QMessageBox.warning(self, "Swap", "Both sides need something: tick purchases or add the goods he brings, "
                                              "and tick sales or pick the goods we give him.")
            return
        try:
            self.result_data = SwapService.record_from_records(
                self.party_id, self.purchase_ids, self.sale_ids, list(self._recv), given, self.note.text())
        except PermissionDenied as e:
            QMessageBox.warning(self, "Not allowed", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Swap not recorded", str(e))
            return
        r = self.result_data
        left = (f"We still owe him {money(r['we_owe_him'])}." if r["we_owe_him"] > 0 else
                f"He still owes us {money(r['he_owes_us'])}." if r["he_owes_us"] > 0 else "Nothing left to pay.")
        QMessageBox.information(self, "Swap recorded", f"Swap recorded. {money(r['offset'])} set off.\n{left}")
        self.accept()
