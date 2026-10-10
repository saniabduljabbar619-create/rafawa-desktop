# apps/product_management_app/features/weigher/views/weigher_station.py
"""
Weighing Station - the weigher's whole job on one screen, in four steps:

    1  Party    pick a registered customer from the list (or a walk-in) / register a new one
    2  Weigh    pick product / area / grade, then type each round straight into the
                rounds table (Bags Round + Weight kg, a new row appears as you go - the same flow as
                the purchase intake); totals are automatic
    3  Price    after the weighing, the price per kg for each product (the usual price is filled in
                where the product has one; type it where it has none) - the amount is kg x price
    4  Receipt  choose the slip type, submit to the cashier, print and sign

Payment and finalization belong to admin/cashier, who also confirm the price. Every round is saved the
moment its row is complete, so a power cut loses nothing. (Edit or remove an earlier weighing from the
Finalize List.)
"""
from __future__ import annotations

import re

from PySide2.QtCore import QTimer, Qt
from PySide2.QtGui import QColor, QFont
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QDoubleSpinBox, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.weigh_service import WeighService
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import Segmented, avatar, style_table
from shared.uix.widgets.product_select import add_select_placeholder
from apps.product_management_app.features.weigher.dialogs.register_party_dialog import RegisterPartyDialog

STEPS = ["1  Party", "2  Weigh", "3  Price", "4  Receipt"]


_LIST_CSS = (
    "QListWidget { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px; outline:0; }"
    "QListWidget::item { padding:10px 12px; border-bottom:1px solid #f1f5f9; }"
    "QListWidget::item:selected { background:#ecfdf5; color:#065f46; }")


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


def _fmt_bags(v) -> str:
    v = float(v or 0)
    return f"{int(v)}" if v == int(v) else f"{v:g}"


def _bags_text(v) -> str:
    v = float(v or 0)
    return f"{_fmt_bags(v)} bag" + ("" if v == 1 else "s")


_BAGS_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:bags?|b)?\s*$", re.IGNORECASE)


def parse_bags(text: str):
    """'3', '3bags', '1 bag', '0.5' -> number of bags (None if it is not one)."""
    m = _BAGS_RE.match(text or "")
    return float(m.group(1).replace(",", ".")) if m else None


def parse_kg(text: str):
    try:
        return float((text or "").replace(",", "").lower().replace("kg", "").strip())
    except ValueError:
        return None


class WeigherStationView(QWidget):
    def __init__(self):
        super().__init__()
        self.ticket_id = 0
        self.party = None                   # dict(id, name, phone, kind)
        self.active_line_id = 0
        self._submitted = False
        self._ticket = {}

        root = make_scrollable_page(self, margins=(16, 14, 16, 14))
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Weighing Station")
        title.setObjectName("Title")
        self.subtitle = QLabel("Register or select who is delivering, then weigh")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        self.step_labels = []
        for text in STEPS:
            lab = QLabel(text)
            lab.setAlignment(Qt.AlignCenter)
            self.step_labels.append(lab)
            head.addWidget(lab)
        root.addLayout(head)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_party_page())
        self.stack.addWidget(self._build_weigh_page())
        self.stack.addWidget(self._build_price_page())
        self.stack.addWidget(self._build_receipt_page())

        self._go(0)
        self._load_party_list()
        self._load_open_tickets()

    # ------------------------------------------------------------------ step header
    def _go(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, lab in enumerate(self.step_labels):
            on = i == index
            done = i < index
            bg, fg, border = ("#0f766e", "#ffffff", "#0f766e") if on else \
                ("#ecfdf5", "#047857", "#a7f3d0") if done else ("#f3f4f6", "#6b7280", "#e5e7eb")
            lab.setStyleSheet(f"background:{bg}; color:{fg}; border:1px solid {border}; border-radius:14px; "
                              f"padding:5px 16px; font-weight:700; font-size:12px;")
        self.subtitle.setText([
            "Register or select who is delivering, then weigh",
            f"{self.party['name'] if self.party else ''} - record each round as it comes off the scale",
            "Set the price per kg for each product - the amount follows",
            "Choose the slip, submit to the cashier, print and sign",
        ][index])

    def _msg(self, text: str, title: str = "Weighing"):
        QMessageBox.warning(self, title, text)

    # ------------------------------------------------------------------ step 1: party
    def _build_party_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setSpacing(14)

        left = _card()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.setSpacing(10)
        cap = QLabel("WHO IS DELIVERING?")
        cap.setObjectName("SectionLabel")
        ll.addWidget(cap)

        crow = QHBoxLayout()
        self.party_cb = QComboBox()
        self.party_cb.setEditable(True)                       # type to filter the drop-down
        self.party_cb.setInsertPolicy(QComboBox.NoInsert)
        self.party_cb.lineEdit().setPlaceholderText("Select a customer (type to search)")
        self.party_cb.setMinimumHeight(36)
        self.party_cb.completer().setFilterMode(Qt.MatchContains)
        self.party_cb.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.btn_register = QPushButton("+ Register New")
        self.btn_register.setMinimumHeight(36)
        crow.addWidget(self.party_cb, 1)
        crow.addWidget(self.btn_register)
        ll.addLayout(crow)

        self.walkin_name = QLineEdit()
        self.walkin_name.setPlaceholderText("Walk-in's name")
        self.walkin_name.setMinimumHeight(36)
        self.walkin_name.setVisible(False)
        ll.addWidget(self.walkin_name)
        ll.addStretch(1)

        self.btn_start = QPushButton("Start Weighing  →")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setEnabled(False)
        ll.addWidget(self.btn_start)
        lay.addWidget(left, 3)

        right = _card()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(10)
        cap2 = QLabel("YOUR OPEN TICKETS")
        cap2.setObjectName("SectionLabel")
        rl.addWidget(cap2)
        hint = QLabel("Weighing that was not finished - pick one to carry on.")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        rl.addWidget(hint)
        self.open_list = QListWidget()
        self.open_list.setStyleSheet(_LIST_CSS)
        rl.addWidget(self.open_list, 1)
        self.btn_resume = QPushButton("Resume Selected")
        self.btn_resume.setEnabled(False)
        rl.addWidget(self.btn_resume)
        lay.addWidget(right, 2)

        self.btn_register.clicked.connect(self._register_party)
        self.party_cb.currentIndexChanged.connect(self._party_changed)
        self.btn_start.clicked.connect(self.start_ticket)
        self.open_list.itemSelectionChanged.connect(lambda: self.btn_resume.setEnabled(bool(self.open_list.selectedItems())))
        self.btn_resume.clicked.connect(self._resume_selected)
        return page

    def _load_party_list(self, *_a, select_id: int = 0):
        """Fill the drop-down: Walk-in first, then every registered customer."""
        cb = self.party_cb
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("Walk-in (no account)", {"walkin": True})
        try:
            rows = WeighService.search_parties("", limit=1000, customers_only=True)
        except Exception as e:
            self._msg(str(e))
            rows = []
        for p in rows:
            cb.addItem(f"{p['name']}    {p['phone']}".rstrip(), p)
            if select_id and p["id"] == select_id:
                cb.setCurrentIndex(cb.count() - 1)
        if not select_id:
            cb.setCurrentIndex(-1)
            cb.clearEditText()
        cb.blockSignals(False)
        self._party_changed()

    def _party_changed(self, *_a):
        data = self.party_cb.currentData()
        walkin = bool(data and data.get("walkin"))
        self.walkin_name.setVisible(walkin)
        self.btn_start.setEnabled(data is not None)

    def _load_open_tickets(self):
        self.open_list.clear()
        try:
            rows = WeighService.list_tickets(status="open")
        except Exception:
            rows = []
        for t in rows:
            it = QListWidgetItem(f"{t['ticket_no']}  -  {t['party_name']}    "
                                 f"{_fmt_bags(t['total_bags'])} bags / {t['total_kg']:,.2f} kg")
            it.setData(Qt.UserRole, int(t["id"]))
            self.open_list.addItem(it)

    def _register_party(self):
        dlg = RegisterPartyDialog(self, kind="customer", lock_kind=True)
        if dlg.exec_() == dlg.Accepted and dlg.party:
            self._load_party_list(select_id=dlg.party["id"])

    def start_ticket(self):
        party = self.party_cb.currentData()
        if party is None:
            self._msg("Select a customer or a walk-in first.")
            return
        name = self.walkin_name.text().strip()
        try:
            if party.get("walkin"):
                from shared.config.constants import WALKIN_PARTY_ID
                self.ticket_id = WeighService.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name=name)
                party = {"id": WALKIN_PARTY_ID, "name": name, "phone": "", "kind": "customer"}
            else:
                self.ticket_id = WeighService.create_ticket(party["id"], "customer")
        except Exception as e:
            self._msg(str(e))
            return
        self.party = party
        self._submitted = False
        self.active_line_id = 0
        self._refresh_ticket()
        self._go(1)

    def _resume_selected(self):
        items = self.open_list.selectedItems()
        if items:
            self.resume_ticket(int(items[0].data(Qt.UserRole)))

    def resume_ticket(self, ticket_id: int):
        try:
            t = WeighService.get_ticket(ticket_id)
        except Exception as e:
            self._msg(str(e))
            return
        self.ticket_id = int(ticket_id)
        self.party = {"id": t["party_id"], "name": t["party_name"], "phone": t["party_phone"], "kind": t["party_role"]}
        self._submitted = False
        self.active_line_id = t["lines"][0]["id"] if t["lines"] else 0
        self._refresh_ticket()
        self._go(1)

    # ------------------------------------------------------------------ step 2: weigh
    def _build_weigh_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setSpacing(14)

        # ---- left: party + products on this ticket
        left = _card()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.setSpacing(10)
        self.party_row = QHBoxLayout()
        self.party_avatar_holder = QVBoxLayout()
        self.party_row.addLayout(self.party_avatar_holder)
        pcol = QVBoxLayout()
        pcol.setSpacing(0)
        self.lbl_party = QLabel("")
        self.lbl_party.setStyleSheet("font-size:15px; font-weight:700;")
        self.lbl_ticket = QLabel("")
        self.lbl_ticket.setStyleSheet("color:#6b7280; font-size:11px;")
        pcol.addWidget(self.lbl_party)
        pcol.addWidget(self.lbl_ticket)
        self.party_row.addLayout(pcol, 1)
        ll.addLayout(self.party_row)

        cap = QLabel("PRODUCT / AREA / GRADE")
        cap.setObjectName("SectionLabel")
        ll.addWidget(cap)
        self.product_cb = QComboBox()
        self.product_cb.setEditable(False)
        self.area_cb = QComboBox()
        self.area_cb.setEditable(True)
        self.area_cb.lineEdit().setPlaceholderText("Area (where it came from)")
        self.grade_cb = QComboBox()
        self.grade_cb.setEditable(True)
        self.grade_cb.lineEdit().setPlaceholderText("Grade")
        for w in (self.product_cb, self.area_cb, self.grade_cb):
            w.setMinimumHeight(34)
            ll.addWidget(w)
        self.btn_add_line = QPushButton("+ Add Product")
        self.btn_add_line.setMinimumHeight(34)
        ll.addWidget(self.btn_add_line)

        self.lines_list = QListWidget()
        self.lines_list.setStyleSheet(_LIST_CSS)
        ll.addWidget(self.lines_list, 1)
        self.btn_remove_line = QPushButton("Remove Product")
        ll.addWidget(self.btn_remove_line)
        lay.addWidget(left, 2)

        # ---- middle: round entry + rounds
        mid = _card()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(16, 14, 16, 14)
        ml.setSpacing(10)
        self.lbl_active = QLabel("Add a product to start weighing")
        self.lbl_active.setStyleSheet("font-size:14px; font-weight:700; color:#0f766e;")
        ml.addWidget(self.lbl_active)

        hint = QLabel("Type each round - bags (3, 3bags or 0.5 for a part-bag) and its weight in kg. "
                      "Press Enter to move on; a new row appears as you go.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        ml.addWidget(hint)

        self._rounds_busy = False
        self.rounds_table = QTableWidget(0, 3)
        self.rounds_table.setHorizontalHeaderLabels(["Round", "Bags Round", "Weight (kg)"])
        self.rounds_table.setEditTriggers(QTableWidget.AllEditTriggers)
        self.rounds_table.setSelectionBehavior(QTableWidget.SelectItems)
        self.rounds_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rounds_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rounds_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.rounds_table.setColumnWidth(0, 70)
        style_table(self.rounds_table, 48)
        self.rounds_table.setStyleSheet(self.rounds_table.styleSheet() + " QTableWidget { font-size:16px; }")
        self.rounds_table.setFocusPolicy(Qt.StrongFocus)
        ml.addWidget(self.rounds_table, 1)

        self.lbl_round_totals = QLabel("Total: 0 bags  -  0.00 kg")
        self.lbl_round_totals.setAlignment(Qt.AlignRight)
        self.lbl_round_totals.setStyleSheet("font-size:15px; font-weight:700; color:#0f766e; padding:2px 6px;")
        ml.addWidget(self.lbl_round_totals)
        self.btn_remove_round = QPushButton("Remove Selected Round")
        ml.addWidget(self.btn_remove_round)
        lay.addWidget(mid, 3)

        # ---- right: totals
        right = _card()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(6)
        cap4 = QLabel("TICKET TOTAL")
        cap4.setObjectName("SectionLabel")
        rl.addWidget(cap4)
        self.lbl_total_bags = QLabel("0")
        self.lbl_total_bags.setStyleSheet("font-size:34px; font-weight:800; color:#0f766e;")
        self.lbl_total_kg = QLabel("0.00 kg")
        self.lbl_total_kg.setStyleSheet("font-size:24px; font-weight:700; color:#111827;")
        bags_cap = QLabel("bags")
        bags_cap.setStyleSheet("color:#6b7280; margin-top:-8px;")
        rl.addWidget(self.lbl_total_bags)
        rl.addWidget(bags_cap)
        rl.addWidget(self.lbl_total_kg)
        self.lbl_rounds_count = QLabel("")
        self.lbl_rounds_count.setStyleSheet("color:#6b7280;")
        rl.addWidget(self.lbl_rounds_count)
        note = QLabel("Next you set the price per kg; the cashier confirms it and takes payment.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9ca3af; font-size:11px; margin-top:10px;")
        rl.addWidget(note)
        rl.addStretch(1)
        self.btn_cancel_ticket = QPushButton("Cancel Ticket")
        self.btn_cancel_ticket.setProperty("role", "danger")
        self.btn_finish = QPushButton("Finish  →  Price")
        self.btn_finish.setObjectName("PrimaryButton")
        self.btn_finish.setMinimumHeight(44)
        rl.addWidget(self.btn_cancel_ticket)
        rl.addWidget(self.btn_finish)
        lay.addWidget(right, 2)

        # data + wiring
        self._load_catalog()
        self.btn_add_line.clicked.connect(self.add_line)
        self.btn_remove_line.clicked.connect(self.remove_line)
        self.lines_list.currentRowChanged.connect(self._on_line_selected)
        self.rounds_table.itemChanged.connect(self._on_round_cell)
        self.btn_remove_round.clicked.connect(self.remove_round)
        self.btn_finish.clicked.connect(self._to_price)
        self.btn_cancel_ticket.clicked.connect(self.cancel_ticket)
        return page

    def _load_catalog(self):
        try:
            crops = WeighService.crop_products()
            ag = WeighService.known_areas_and_grades()
        except Exception:
            crops, ag = [], {"areas": [], "grades": ["A", "B", "C", "Mixed"]}
        self.product_cb.clear()
        for p in crops:
            self.product_cb.addItem(f"{p['name']}", p["id"])
        add_select_placeholder(self.product_cb)          # never starts on a real product
        self.area_cb.clear()
        self.area_cb.addItems(ag["areas"])
        self.area_cb.setCurrentIndex(-1)
        self.grade_cb.clear()
        self.grade_cb.addItems(ag["grades"])
        self.grade_cb.setCurrentIndex(-1)

    # ---- ticket rendering
    def _refresh_ticket(self):
        if not self.ticket_id:
            return
        try:
            t = WeighService.get_ticket(self.ticket_id)
        except Exception as e:
            self._msg(str(e))
            return
        self._ticket = t
        while self.party_avatar_holder.count():
            w = self.party_avatar_holder.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.party_avatar_holder.addWidget(avatar(t["party_name"], 40))
        self.lbl_party.setText(t["party_name"])
        self.lbl_ticket.setText(f"{t['ticket_no']}  -  {t['party_role'].title()}  -  {t['party_phone']}")

        keep = self.active_line_id
        self.lines_list.blockSignals(True)
        self.lines_list.clear()
        for ln in t["lines"]:
            tag = " / ".join(x for x in (ln["area"], ln["grade"] and f"Grade {ln['grade']}") if x)
            it = QListWidgetItem(f"{ln['product_name']}" + (f"  ({tag})" if tag else "") +
                                 f"\n{_fmt_bags(ln['bags'])} bags  -  {ln['kg']:,.2f} kg")
            it.setData(Qt.UserRole, int(ln["id"]))
            self.lines_list.addItem(it)
            if int(ln["id"]) == keep:
                self.lines_list.setCurrentItem(it)
        if self.lines_list.currentRow() < 0 and self.lines_list.count():
            self.lines_list.setCurrentRow(self.lines_list.count() - 1)
        self.lines_list.blockSignals(False)
        cur = self.lines_list.currentItem()
        self.active_line_id = int(cur.data(Qt.UserRole)) if cur else 0
        self._render_rounds()

        self.lbl_total_bags.setText(_fmt_bags(t["total_bags"]))
        self.lbl_total_kg.setText(f"{t['total_kg']:,.2f} kg")
        n = sum(len(ln["rounds"]) for ln in t["lines"])
        self.lbl_rounds_count.setText(f"{n} round{'s' if n != 1 else ''} recorded")

    def _active_line(self):
        for ln in getattr(self, "_ticket", {}).get("lines", []):
            if int(ln["id"]) == self.active_line_id:
                return ln
        return None

    def _render_rounds(self):
        ln = self._active_line()
        self._rounds_busy = True
        try:
            self.rounds_table.setRowCount(0)
            if ln is None:
                self.lbl_active.setText("Add a product to start weighing")
                self.lbl_round_totals.setText("Total: 0 bags  -  0.00 kg")
                return
            tag = " / ".join(x for x in (ln["area"], ln["grade"] and f"Grade {ln['grade']}") if x)
            self.lbl_active.setText(f"Weighing: {ln['product_name']}" + (f"  ({tag})" if tag else ""))
            for r in ln["rounds"]:
                self._add_round_row(r["round_no"], r["id"], _bags_text(r["bags"]), f"{r['weight_kg']:.2f}")
            self._add_round_row(len(ln["rounds"]) + 1, None, "", "")      # the next round, ready to type
            self.lbl_round_totals.setText(f"Total: {_fmt_bags(ln['bags'])} bags  -  {ln['kg']:,.2f} kg")
        finally:
            self._rounds_busy = False

    def _add_round_row(self, number: int, round_id, bags_text: str, kg_text: str):
        t = self.rounds_table
        row = t.rowCount()
        t.insertRow(row)
        num = QTableWidgetItem(str(number))
        num.setFlags(Qt.ItemIsEnabled)
        num.setTextAlignment(Qt.AlignCenter)
        num.setData(Qt.UserRole, round_id)
        if round_id is None:
            num.setForeground(QColor("#9ca3af"))
        t.setItem(row, 0, num)
        for col, text in ((1, bags_text), (2, kg_text)):
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(row, col, it)

    def _focus_cell(self, row: int, col: int):
        t = self.rounds_table
        if 0 <= row < t.rowCount() and t.item(row, col) is not None:
            t.setCurrentCell(row, col)
            t.editItem(t.item(row, col))

    def _on_round_cell(self, item):
        """A cell was typed into: record the round when both bags and kg are there, or correct it."""
        if self._rounds_busy or item.column() == 0 or not self.active_line_id:
            return
        t = self.rounds_table
        row = item.row()
        rid = t.item(row, 0).data(Qt.UserRole)
        bags_txt = (t.item(row, 1).text() if t.item(row, 1) else "").strip()
        kg_txt = (t.item(row, 2).text() if t.item(row, 2) else "").strip()

        if rid is None:                                           # the blank row at the bottom
            if not bags_txt or not kg_txt:                        # half typed: go to the other cell
                other = 2 if item.column() == 1 else 1
                QTimer.singleShot(0, lambda: self._focus_cell(row, other))
                return
            bags, kg = parse_bags(bags_txt), parse_kg(kg_txt)
            problem = ("Type the bags as a number, e.g. 3, 3bags or 0.5." if bags is None else
                       "Type the weight in kg, e.g. 245.5." if kg is None else "")
            if not problem:
                try:
                    WeighService.record_round(self.active_line_id, bags, kg)
                except Exception as e:
                    problem = str(e)
            if problem:
                self._msg(problem)
                col = 1 if bags is None or (kg is not None and "bag" in problem.lower()) else 2
                QTimer.singleShot(0, lambda: self._focus_cell(row, col))
                return
            QTimer.singleShot(0, lambda: self._after_round_saved(new_row=True))
            return

        # an already-saved round was changed
        bags, kg = parse_bags(bags_txt), parse_kg(kg_txt)
        try:
            if bags is None or kg is None:
                raise ValueError("Bags and weight must both be numbers.")
            WeighService.update_round(int(rid), bags=bags, weight_kg=kg)
        except Exception as e:
            self._msg(str(e))
        QTimer.singleShot(0, lambda: self._after_round_saved(new_row=False))

    def _after_round_saved(self, new_row: bool):
        self._refresh_ticket()
        if new_row:
            self._focus_cell(self.rounds_table.rowCount() - 1, 1)

    def _on_line_selected(self, row: int):
        it = self.lines_list.item(row) if row >= 0 else None
        self.active_line_id = int(it.data(Qt.UserRole)) if it else 0
        self._render_rounds()

    # ---- actions
    def add_line(self):
        pid = self.product_cb.currentData()
        if pid is None:
            self._msg("Select a product first.")
            return
        try:
            self.active_line_id = WeighService.add_line(
                self.ticket_id, int(pid), self.area_cb.currentText(), self.grade_cb.currentText())
        except Exception as e:
            self._msg(str(e))
            return
        self.product_cb.setCurrentIndex(0)
        self._refresh_ticket()
        QTimer.singleShot(0, lambda: self._focus_cell(self.rounds_table.rowCount() - 1, 1))

    def remove_line(self):
        if not self.active_line_id:
            return
        ln = self._active_line()
        if ln and ln["rounds"]:
            ok = QMessageBox.question(self, "Remove Product",
                                      f"Remove {ln['product_name']} and its {len(ln['rounds'])} recorded rounds?",
                                      QMessageBox.Yes | QMessageBox.No)
            if ok != QMessageBox.Yes:
                return
        try:
            WeighService.remove_line(self.active_line_id)
        except Exception as e:
            self._msg(str(e))
            return
        self.active_line_id = 0
        self._refresh_ticket()

    def remove_round(self):
        row = self.rounds_table.currentRow()
        it = self.rounds_table.item(row, 0) if row >= 0 else None
        if it is None or it.data(Qt.UserRole) is None:
            self._msg("Select a recorded round to remove first.")
            return
        try:
            WeighService.remove_round(int(it.data(Qt.UserRole)))
        except Exception as e:
            self._msg(str(e))
            return
        self._refresh_ticket()

    def cancel_ticket(self):
        ok = QMessageBox.question(self, "Cancel Ticket", "Cancel this weighing ticket? Nothing will be recorded.",
                                  QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            WeighService.cancel_ticket(self.ticket_id, "Cancelled by weigher")
        except Exception as e:
            self._msg(str(e))
            return
        self.new_weighing()

    # ------------------------------------------------------------------ step 3: price
    def _build_price_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(10)
        card = _card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)
        cap = QLabel("PRICE PER KG - for each product weighed")
        cap.setObjectName("SectionLabel")
        cl.addWidget(cap)
        self.lbl_price_party = QLabel("")
        self.lbl_price_party.setStyleSheet("font-size:15px; font-weight:700;")
        cl.addWidget(self.lbl_price_party)
        self.price_table = QTableWidget(0, 6)
        self.price_table.setHorizontalHeaderLabels(["Product", "Area / Grade", "Bags", "Weight (kg)",
                                                    "Price per kg", "Amount"])
        self.price_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.price_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.price_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        style_table(self.price_table, 52)
        cl.addWidget(self.price_table, 1)
        hint = QLabel("The usual price is filled in where the product has one - change it if today's price is "
                      "different. Where there is none (amber), type it. The cashier confirms the price before paying.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        cl.addWidget(hint)
        tot = QHBoxLayout()
        self.lbl_price_totals = QLabel("")
        self.lbl_price_totals.setStyleSheet("font-size:13px; color:#374151;")
        tot.addWidget(self.lbl_price_totals, 1)
        self.lbl_price_amount = QLabel("₦0.00")
        self.lbl_price_amount.setStyleSheet("font-size:26px; font-weight:800; color:#0f766e;")
        tot.addWidget(self.lbl_price_amount)
        cl.addLayout(tot)
        lay.addWidget(card, 1)
        row = QHBoxLayout()
        self.btn_price_back = QPushButton("←  Back to Weighing")
        self.btn_price_next = QPushButton("Next  →  Receipt")
        self.btn_price_next.setObjectName("PrimaryButton")
        self.btn_price_next.setMinimumHeight(44)
        row.addWidget(self.btn_price_back)
        row.addStretch(1)
        row.addWidget(self.btn_price_next)
        lay.addLayout(row)
        self._price_spins = {}
        self.btn_price_back.clicked.connect(lambda: self._go(1))
        self.btn_price_next.clicked.connect(self._price_next)
        return page

    def _to_price(self):
        self._refresh_ticket()
        t = self._ticket
        if not any(ln["rounds"] for ln in t["lines"]):
            self._msg("Record at least one round before finishing.")
            return
        try:
            usual = {p["id"]: p["price"] for p in WeighService.crop_products()}
        except Exception:
            usual = {}
        self.lbl_price_party.setText(f"{t['party_name']}   -   {t['ticket_no']}")
        self.price_table.setRowCount(0)
        self._price_spins = {}
        for ln in (x for x in t["lines"] if x["rounds"]):
            r = self.price_table.rowCount()
            self.price_table.insertRow(r)
            tag = " / ".join(x for x in (ln["area"], ln["grade"] and f"Grade {ln['grade']}") if x)
            for c, v in enumerate((ln["product_name"], tag, _fmt_bags(ln["bags"]), f"{ln['kg']:,.2f}")):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.price_table.setItem(r, c, it)
            spin = QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(0, 10_000_000)
            spin.setPrefix("₦")
            have = float(ln["unit_price"] or 0) or float(usual.get(int(ln["product_id"]), 0) or 0)
            spin.setValue(have)
            spin.setProperty("line_id", int(ln["id"]))
            spin.setToolTip("Usual price - change it if today's is different" if have else
                            "No usual price for this product - type today's price")
            spin.setStyleSheet("" if have else "QDoubleSpinBox { background:#fff7ed; }")
            spin.valueChanged.connect(self._price_changed)
            self.price_table.setCellWidget(r, 4, spin)
            amt = QTableWidgetItem("")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.price_table.setItem(r, 5, amt)
            self._price_spins[int(ln["id"])] = spin
        self.lbl_price_totals.setText(f"{_fmt_bags(t['total_bags'])} bags   -   {t['total_kg']:,.2f} kg")
        self._price_changed()
        self._go(2)

    def _price_changed(self, *_a):
        total = 0.0
        lines = [ln for ln in self._ticket.get("lines", []) if ln["rounds"]]
        for r, ln in enumerate(lines):
            spin = self._price_spins.get(int(ln["id"]))
            if spin is None:
                continue
            price = spin.value()
            amount = round(ln["kg"] * price, 2)
            total += amount
            self.price_table.item(r, 5).setText(f"₦{amount:,.2f}" if price > 0 else "-")
            if price > 0:
                spin.setStyleSheet("")
        self.lbl_price_amount.setText(f"₦{total:,.2f}")

    def _price_next(self):
        prices = {lid: spin.value() for lid, spin in self._price_spins.items()}
        lines = {int(ln["id"]): ln for ln in self._ticket.get("lines", [])}
        missing = [lines[lid]["product_name"] for lid, p in prices.items() if p <= 0 and lid in lines]
        if missing:
            for lid, spin in self._price_spins.items():
                if spin.value() <= 0:
                    spin.setStyleSheet("QDoubleSpinBox { background:#fde9c8; }")
            self._msg("Enter the price per kg for: " + ", ".join(missing), "Price")
            return
        try:
            WeighService.set_ticket_prices(self.ticket_id, prices)
        except Exception as e:
            self._msg(str(e), "Price")
            return
        self._to_receipt()

    # ------------------------------------------------------------------ step 4: receipt
    def _build_receipt_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setSpacing(14)

        left = _card()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.setSpacing(10)
        cap = QLabel("RECEIPT TYPE")
        cap.setObjectName("SectionLabel")
        ll.addWidget(cap)
        self.receipt_seg = Segmented([("weighing", "Option 1 - Weighing Receipt"),
                                      ("value", "Option 2 - Full Value Receipt")])
        ll.addWidget(self.receipt_seg)
        self.lbl_receipt_totals = QLabel("")
        self.lbl_receipt_totals.setStyleSheet("font-size:15px; font-weight:700; color:#0f766e; margin-top:6px;")
        self.lbl_receipt_totals.setWordWrap(True)
        ll.addWidget(self.lbl_receipt_totals)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#6b7280;")
        ll.addWidget(self.lbl_status)
        ll.addStretch(1)
        self.btn_back = QPushButton("←  Back to Price")
        self.btn_submit = QPushButton("Submit to Cashier")
        self.btn_submit.setObjectName("PrimaryButton")
        self.btn_submit.setMinimumHeight(44)
        self.btn_print = QPushButton("Print Receipt")
        self.btn_print.setVisible(False)
        self.btn_new = QPushButton("New Weighing")
        self.btn_new.setObjectName("PrimaryButton")
        self.btn_new.setMinimumHeight(44)
        self.btn_new.setVisible(False)
        for b in (self.btn_submit, self.btn_print, self.btn_new, self.btn_back):
            ll.addWidget(b)
        lay.addWidget(left, 2)

        right = _card()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 14, 16, 14)
        cap2 = QLabel("RECEIPT PREVIEW - nothing prints until you press Print Receipt")
        cap2.setObjectName("SectionLabel")
        rl.addWidget(cap2)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.preview.setFont(mono)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        rl.addWidget(self.preview, 1)
        lay.addWidget(right, 3)

        self.receipt_seg.changed.connect(lambda _k: self._render_preview())
        self.btn_back.clicked.connect(lambda: self._go(2))
        self.btn_submit.clicked.connect(self.submit)
        self.btn_print.clicked.connect(self._print_current)
        self.btn_new.clicked.connect(self.new_weighing)
        return page

    def _slip_text(self) -> str:
        from apps.product_management_app.features.receipts_v2 import get_shop, render_weigh_slip
        return render_weigh_slip(self._ticket, get_shop(), self.receipt_seg.key())

    def _render_preview(self):
        if getattr(self, "_ticket", None):
            self.preview.setPlainText(self._slip_text())

    def _to_receipt(self):
        self._refresh_ticket()
        t = self._ticket
        if not any(ln["rounds"] for ln in t["lines"]):
            self._msg("Record at least one round before finishing.")
            return
        self.lbl_receipt_totals.setText(
            f"{t['party_name']}\n{_fmt_bags(t['total_bags'])} bags  -  {t['total_kg']:,.2f} kg"
            + (f"  -  ₦{t['total_amount']:,.2f}" if t.get("total_amount") else ""))
        self.lbl_status.setText("Submitting sends this ticket to the cashier, who confirms the price and pays.")
        self._submitted = False
        self.btn_submit.setVisible(True)
        self.btn_submit.setEnabled(True)
        self.btn_back.setVisible(True)
        self.btn_print.setVisible(False)
        self.btn_new.setVisible(False)
        self.receipt_seg.setEnabled(True)
        self._render_preview()
        self._go(3)

    def submit(self):
        try:
            self._ticket = WeighService.submit_ticket(self.ticket_id, self.receipt_seg.key())
        except Exception as e:
            self._msg(str(e))
            return
        self._submitted = True
        self.btn_submit.setVisible(False)
        self.btn_back.setVisible(False)
        self.btn_print.setVisible(True)
        self.btn_new.setVisible(True)
        self.lbl_status.setText(f"✔ {self._ticket['ticket_no']} submitted to the cashier.\n"
                                "Pick Option 1 or Option 2 and press Print Receipt when you need it.")
        self._render_preview()
        self._load_open_tickets()

    def _print_current(self):
        from apps.product_management_app.features.receipts_v2 import receipt_print
        try:
            receipt_print.print_text(self, self._slip_text(), window_title=f"Weighing Receipt - {self._ticket['ticket_no']}")
        except Exception as e:
            self._msg(f"The receipt could not be printed:\n{e}\n\nThe ticket is saved - press Print Receipt to try again.", "Print")

    def new_weighing(self):
        self.ticket_id = 0
        self.party = None
        self.active_line_id = 0
        self._submitted = False
        self._ticket = {}
        self.walkin_name.clear()
        self._load_party_list()
        self._load_open_tickets()
        self._load_catalog()
        self._go(0)
