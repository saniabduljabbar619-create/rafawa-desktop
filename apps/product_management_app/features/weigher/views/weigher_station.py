# apps/product_management_app/features/weigher/views/weigher_station.py
"""
Weighing Station - the weigher's whole job on one screen, in three steps:

    1  Party    register or select the customer / supplier
    2  Weigh    pick product / area / grade, then record each round (1-3 or a partial bag + kg);
                totals are automatic
    3  Receipt  choose the slip type, submit to the cashier, print and sign

Nothing here shows or asks for a price: that, payment and finalization belong to admin/cashier.
Every round is saved the moment it is recorded, so a power cut loses nothing.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtGui import QDoubleValidator, QFont
from PySide2.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.weigh_service import MAX_BAGS_PER_ROUND, WeighService
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import Segmented, avatar, chip, style_table
from shared.uix.widgets.product_select import add_select_placeholder
from apps.product_management_app.features.weigher.dialogs.register_party_dialog import RegisterPartyDialog

STEPS = ["1  Party", "2  Weigh", "3  Receipt"]


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


def _fmt_bags(v) -> str:
    v = float(v or 0)
    return f"{int(v)}" if v == int(v) else f"{v:g}"


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

        row = QHBoxLayout()
        row.addWidget(QLabel("Delivering as"))
        self.kind_seg = Segmented([("supplier", "Supplier"), ("customer", "Customer")])
        row.addWidget(self.kind_seg)
        row.addStretch(1)
        ll.addLayout(row)

        srow = QHBoxLayout()
        self.party_search = QLineEdit()
        self.party_search.setPlaceholderText("Search name or phone")
        self.party_search.setClearButtonEnabled(True)
        self.party_search.setMinimumHeight(36)
        self.btn_register = QPushButton("+ Register New")
        self.btn_register.setMinimumHeight(36)
        srow.addWidget(self.party_search, 1)
        srow.addWidget(self.btn_register)
        ll.addLayout(srow)

        self.party_list = QListWidget()
        self.party_list.setStyleSheet(
            "QListWidget { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px; outline:0; }"
            "QListWidget::item { padding:10px 12px; border-bottom:1px solid #f1f5f9; }"
            "QListWidget::item:selected { background:#ecfdf5; color:#065f46; }")
        ll.addWidget(self.party_list, 1)

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
        self.open_list.setStyleSheet(self.party_list.styleSheet())
        rl.addWidget(self.open_list, 1)
        self.btn_resume = QPushButton("Resume Selected")
        self.btn_resume.setEnabled(False)
        rl.addWidget(self.btn_resume)
        lay.addWidget(right, 2)

        self.party_search.textChanged.connect(self._load_party_list)
        self.btn_register.clicked.connect(self._register_party)
        self.party_list.itemSelectionChanged.connect(lambda: self.btn_start.setEnabled(bool(self.party_list.selectedItems())))
        self.party_list.itemDoubleClicked.connect(lambda _i: self.start_ticket())
        self.btn_start.clicked.connect(self.start_ticket)
        self.open_list.itemSelectionChanged.connect(lambda: self.btn_resume.setEnabled(bool(self.open_list.selectedItems())))
        self.btn_resume.clicked.connect(self._resume_selected)
        return page

    def _load_party_list(self, *_a, select_id: int = 0):
        self.party_list.clear()
        try:
            rows = WeighService.search_parties(self.party_search.text())
        except Exception as e:
            self._msg(str(e))
            return
        for p in rows:
            it = QListWidgetItem(f"{p['name']}    {p['phone']}    [{p['kind']}]")
            it.setData(Qt.UserRole, p)
            self.party_list.addItem(it)
            if select_id and p["id"] == select_id:
                it.setSelected(True)
                self.party_list.setCurrentItem(it)

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
        dlg = RegisterPartyDialog(self, kind=self.kind_seg.key())
        if dlg.exec_() == dlg.Accepted and dlg.party:
            self.party_search.clear()
            self._load_party_list(select_id=dlg.party["id"])

    def start_ticket(self):
        items = self.party_list.selectedItems()
        if not items:
            self._msg("Select a customer or supplier first.")
            return
        party = items[0].data(Qt.UserRole)
        try:
            self.ticket_id = WeighService.create_ticket(party["id"], self.kind_seg.key())
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
        self.kind_seg.set_key(t["party_role"])
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
        self.lines_list.setStyleSheet(self.party_list.styleSheet() if hasattr(self, "party_list") else "")
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

        cap2 = QLabel("BAGS ON THE SCALE")
        cap2.setObjectName("SectionLabel")
        ml.addWidget(cap2)
        brow = QHBoxLayout()
        self.bag_group = QButtonGroup(self)
        self.bag_group.setExclusive(True)
        self.bag_buttons = {}
        big = ("QPushButton { background:#ffffff; border:2px solid #d1d5db; border-radius:12px; font-size:18px;"
               " font-weight:700; padding:14px 8px; color:#374151; } QPushButton:hover { background:#f3f4f6; }"
               " QPushButton:checked { background:#0f766e; color:#ffffff; border-color:#0f766e; }")
        for n in range(1, MAX_BAGS_PER_ROUND + 1):
            b = QPushButton(f"{n} bag" + ("s" if n > 1 else ""))
            b.setCheckable(True)
            b.setProperty("role", "plain")
            b.setStyleSheet(big)
            b.setCursor(Qt.PointingHandCursor)
            self.bag_group.addButton(b)
            self.bag_buttons[n] = b
            brow.addWidget(b)
        self.btn_partial = QPushButton("Partial")
        self.btn_partial.setCheckable(True)
        self.btn_partial.setProperty("role", "plain")
        self.btn_partial.setStyleSheet(big)
        self.bag_group.addButton(self.btn_partial)
        brow.addWidget(self.btn_partial)
        ml.addLayout(brow)
        self.bag_buttons[1].setChecked(True)

        prow = QHBoxLayout()
        self.partial_lbl = QLabel("Part of a bag:")
        self.partial_spin = QDoubleSpinBox()
        self.partial_spin.setDecimals(2)
        self.partial_spin.setRange(0.05, MAX_BAGS_PER_ROUND)
        self.partial_spin.setSingleStep(0.25)
        self.partial_spin.setValue(0.5)
        self.partial_spin.setSuffix(" bag")
        prow.addWidget(self.partial_lbl)
        prow.addWidget(self.partial_spin)
        prow.addStretch(1)
        ml.addLayout(prow)
        self.partial_lbl.setVisible(False)
        self.partial_spin.setVisible(False)

        cap3 = QLabel("WEIGHT")
        cap3.setObjectName("SectionLabel")
        ml.addWidget(cap3)
        wrow = QHBoxLayout()
        self.weight_in = QLineEdit()
        self.weight_in.setPlaceholderText("0.00")
        self.weight_in.setValidator(QDoubleValidator(0.0, 100000.0, 2, self))
        self.weight_in.setAlignment(Qt.AlignRight)
        f = QFont()
        f.setPointSize(22)
        f.setBold(True)
        self.weight_in.setFont(f)
        self.weight_in.setMinimumHeight(56)
        kg = QLabel("kg")
        kg.setStyleSheet("font-size:20px; font-weight:700; color:#6b7280;")
        wrow.addWidget(self.weight_in, 1)
        wrow.addWidget(kg)
        ml.addLayout(wrow)
        self.btn_record = QPushButton("Record Round")
        self.btn_record.setObjectName("PrimaryButton")
        self.btn_record.setMinimumHeight(46)
        ml.addWidget(self.btn_record)

        self.rounds_table = QTableWidget(0, 4)
        self.rounds_table.setHorizontalHeaderLabels(["Round", "Bags", "Weight (kg)", "Time"])
        self.rounds_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rounds_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rounds_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rounds_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        style_table(self.rounds_table, 34)
        ml.addWidget(self.rounds_table, 1)
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
        note = QLabel("Price and payment are handled by the cashier.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9ca3af; font-size:11px; margin-top:10px;")
        rl.addWidget(note)
        rl.addStretch(1)
        self.btn_cancel_ticket = QPushButton("Cancel Ticket")
        self.btn_cancel_ticket.setProperty("role", "danger")
        self.btn_finish = QPushButton("Finish  →  Receipt")
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
        self.btn_partial.toggled.connect(self._on_partial_toggled)
        self.btn_record.clicked.connect(self.record_round)
        self.weight_in.returnPressed.connect(self.record_round)
        self.btn_remove_round.clicked.connect(self.remove_round)
        self.btn_finish.clicked.connect(self._to_receipt)
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

    def _on_partial_toggled(self, on: bool):
        self.partial_lbl.setVisible(on)
        self.partial_spin.setVisible(on)

    def bags_value(self) -> float:
        if self.btn_partial.isChecked():
            return float(self.partial_spin.value())
        for n, b in self.bag_buttons.items():
            if b.isChecked():
                return float(n)
        return 1.0

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
        self.rounds_table.setRowCount(0)
        if ln is None:
            self.lbl_active.setText("Add a product to start weighing")
            return
        tag = " / ".join(x for x in (ln["area"], ln["grade"] and f"Grade {ln['grade']}") if x)
        self.lbl_active.setText(f"Weighing: {ln['product_name']}" + (f"  ({tag})" if tag else ""))
        for r in ln["rounds"]:
            row = self.rounds_table.rowCount()
            self.rounds_table.insertRow(row)
            vals = [str(r["round_no"]), _fmt_bags(r["bags"]), f"{r['weight_kg']:,.2f}",
                    r["created_at"].strftime("%H:%M:%S") if r.get("created_at") else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignCenter if c in (0, 1) else Qt.AlignRight | Qt.AlignVCenter)
                it.setData(Qt.UserRole, int(r["id"]))
                self.rounds_table.setItem(row, c, it)
        self.rounds_table.scrollToBottom()

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
        self.weight_in.setFocus()

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

    def record_round(self):
        if not self.active_line_id:
            self._msg("Add a product first, then record its rounds.")
            return
        try:
            kg = float((self.weight_in.text() or "0").replace(",", ""))
        except ValueError:
            kg = 0.0
        try:
            WeighService.record_round(self.active_line_id, self.bags_value(), kg)
        except Exception as e:
            self._msg(str(e))
            return
        self.weight_in.clear()
        self._refresh_ticket()
        self.weight_in.setFocus()

    def remove_round(self):
        row = self.rounds_table.currentRow()
        it = self.rounds_table.item(row, 0) if row >= 0 else None
        if it is None:
            self._msg("Select the round to remove first.")
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

    # ------------------------------------------------------------------ step 3: receipt
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
        self.receipt_seg = Segmented([("full", "Full slip (every round)"), ("summary", "Summary (totals only)")])
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
        self.btn_back = QPushButton("←  Back to Weighing")
        self.btn_submit = QPushButton("Submit && Print Slip")
        self.btn_submit.setObjectName("PrimaryButton")
        self.btn_submit.setMinimumHeight(44)
        self.btn_print = QPushButton("Print Again")
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
        cap2 = QLabel("SLIP PREVIEW - signed by the weigher and the customer / supplier")
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
        self.btn_back.clicked.connect(lambda: self._go(1))
        self.btn_submit.clicked.connect(self.submit_and_print)
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
            f"{t['party_name']}\n{_fmt_bags(t['total_bags'])} bags  -  {t['total_kg']:,.2f} kg")
        self.lbl_status.setText("Submitting sends this ticket to the cashier, who confirms the price and pays.")
        self._submitted = False
        self.btn_submit.setVisible(True)
        self.btn_submit.setEnabled(True)
        self.btn_back.setVisible(True)
        self.btn_print.setVisible(False)
        self.btn_new.setVisible(False)
        self.receipt_seg.setEnabled(True)
        self._render_preview()
        self._go(2)

    def submit_and_print(self):
        try:
            self._ticket = WeighService.submit_ticket(self.ticket_id, self.receipt_seg.key())
        except Exception as e:
            self._msg(str(e))
            return
        self._submitted = True
        self.btn_submit.setVisible(False)
        self.btn_back.setVisible(False)
        self.receipt_seg.setEnabled(False)
        self.btn_print.setVisible(True)
        self.btn_new.setVisible(True)
        self.lbl_status.setText(f"✔ {self._ticket['ticket_no']} submitted to the cashier.\n"
                                "Have the slip signed by the weigher and the customer / supplier.")
        self._render_preview()
        self._load_open_tickets()
        self._print_current()

    def _print_current(self):
        from apps.product_management_app.features.receipts_v2 import receipt_print
        try:
            receipt_print.print_text(self, self._slip_text(), window_title=f"Weighing Slip - {self._ticket['ticket_no']}")
        except Exception as e:
            self._msg(f"The slip could not be printed:\n{e}\n\nThe ticket is saved - use Print Again.", "Print")

    def new_weighing(self):
        self.ticket_id = 0
        self.party = None
        self.active_line_id = 0
        self._submitted = False
        self._ticket = {}
        self.party_search.clear()
        self.party_list.clearSelection()
        self.btn_start.setEnabled(False)
        self.weight_in.clear()
        self._load_party_list()
        self._load_open_tickets()
        self._load_catalog()
        self._go(0)
