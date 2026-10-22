# apps/product_management_app/features/orders/views/order_sheet_page.py
"""
An order as a sheet - Sales Order or Purchase Order, one screen for the whole life of the order:

    Draft  ->  On the road  ->  Delivered & confirmed  ->  Paid

    Order lines tab   type the lines into the grid (draft); enter what the other side actually received,
                      rejected and the price (on the road); read the final figures (confirmed)
    Payments tab      type a payment into the last row; remove = void (with a reason)
    Other money tab   cash they gave us / we gave them, outside the order price; can be applied to the order
    Expenses tab      what we paid for this order (driver, fuel, food ...), charged to the partner or our own cost,
                      with the repayments they make
    History tab       who did what, and when, including payments

Stage actions sit in the toolbar (Dispatch / Mark in transit, Fill from order, Confirm delivery, Revert,
Cancel). The rules - stock movements, validation, totals - live in the order services; this only lays the
sheet out and calls OrderSheetService.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from PySide2.QtCore import QDate, Qt, Signal
from PySide2.QtWidgets import (
    QCheckBox, QDateEdit, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget,
    QTabBar, QVBoxLayout, QWidget,
)

from database.services.order_money_service import EXPENSE_CATEGORIES, OrderMoneyService
from database.services.order_sheet_service import REVIEW_CATEGORIES, OrderSheetService, estimate
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import avatar, chip, money
from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.debt_center.views.sheet_grid import SheetGrid

STAGES = {
    "sales": ["1  Draft", "2  On the road", "3  Delivered", "4  Paid"],
    "purchase": ["1  Draft", "2  On the road", "3  Received", "4  Paid"],
}
STATUS_TEXT = {"draft": "Draft", "in_transit": "On the road", "confirmed": "Confirmed", "cancelled": "Cancelled"}
STATUS_TONE = {"draft": "grey", "in_transit": "blue", "confirmed": "green", "cancelled": "red"}
METHOD_OPTIONS = ["Cash", "Transfer", "POS", "Cheque"]
MONEY_WAYS = ["They gave us", "We gave them"]
EXPENSE_CHARGE = ["They pay back", "Our cost"]
WORDS = {
    "sales": {"partner": "Company", "go": "Dispatch", "go_done": "Dispatched", "rcv": "Company received",
              "price": "Company price (₦)", "confirm": "Confirm delivery", "ordered": "Sent",
              "plain": "Sales order - we sell to this company"},
    "purchase": {"partner": "Supplier", "go": "Mark in transit", "go_done": "On the road", "rcv": "We received",
                 "price": "Price paid (₦)", "confirm": "Confirm receipt", "ordered": "Ordered",
                 "plain": "Purchase order - we buy from this supplier"},
}


def _num(raw) -> Optional[float]:
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        raise ValueError("Type a number.")


class OrderSheetPage(QWidget):
    back = Signal()
    changed = Signal()
    opened = Signal(int)                     # a new order was created: its id

    def __init__(self, kind: str, partner_id: int, order_id: Optional[int] = None, is_admin: bool = True):
        super().__init__()
        self.kind, self.partner_id, self.order_id, self.is_admin = kind, int(partner_id), order_id, is_admin
        self.w = WORDS[kind]
        self.d: Optional[Dict[str, Any]] = None
        self.lines: List[Dict[str, Any]] = []          # draft lines held here until the order exists
        self.products = {p["id"]: p for p in OrderSheetService.products(kind)}
        self.labels = {f"{p['name']}  ({p['sku']})": p["id"] for p in self.products.values()}
        self.partner = OrderSheetService.partner(kind, partner_id) or {"name": "?", "phone": "", "address": ""}
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        self._build()
        self.reload()

    # ================================================================== layout
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.btn_back = QPushButton(f"<  {self.partner['name']}")
        self.btn_back.setObjectName("dcBack")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        self.head_slot = QHBoxLayout()
        head.addLayout(self.head_slot)
        head.addStretch(1)
        root.addLayout(head)

        self.steps = QHBoxLayout()
        self.steps.setSpacing(6)
        self.step_labels = []
        for text in STAGES[self.kind]:
            lab = QLabel(text)
            self.step_labels.append(lab)
            self.steps.addWidget(lab)
        self.steps.addStretch(1)
        self.btn_next = QPushButton("")
        self.btn_next.setObjectName("msBig")
        self.btn_next.setMinimumHeight(30)
        self.steps.addWidget(self.btn_next)
        root.addLayout(self.steps)
        self.lbl_next = QLabel("")
        self.lbl_next.setObjectName("dcSub")
        self.lbl_next.setWordWrap(True)
        self.lbl_next.setStyleSheet("color:#1c2333; font-size:13.5px; font-weight:600;")
        root.addWidget(self.lbl_next)

        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_value = kpi("Order value", dark=True)
        self.k_paid = kpi("Paid so far")
        self.k_balance = kpi("Still to pay" if self.kind == "purchase" else "Still to receive")
        self.k_lines = kpi("Products")
        for k in (self.k_value, self.k_paid, self.k_balance, self.k_lines):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        info = QHBoxLayout()
        info.setSpacing(8)
        self.lbl_expected = QLabel("Expected delivery (optional)")
        self.lbl_expected.setObjectName("dcSub")
        self.chk_expected = QCheckBox()
        self.expected = QDateEdit(QDate.currentDate())
        self.expected.setCalendarPopup(True)
        self.expected.setDisplayFormat("dd/MM/yyyy")
        self.lbl_notes = QLabel("Notes")
        self.lbl_notes.setObjectName("dcSub")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes for this order (optional)")
        self.lbl_meta = QLabel("")
        self.lbl_meta.setObjectName("dcSub")
        for wd in (self.lbl_expected, self.chk_expected, self.expected, self.lbl_notes):
            info.addWidget(wd)
        info.addWidget(self.notes, 1)
        info.addWidget(self.lbl_meta)
        root.addLayout(info)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ Add line")
        self.btn_add.setObjectName("msBig")
        self.btn_remove = QPushButton("Remove line")
        self.btn_remove.setObjectName("dcGhost")
        self.btn_go = QPushButton(self.w["go"])
        self.btn_go.setObjectName("msPay")
        self.btn_fill = QPushButton("Fill from order")
        self.btn_fill.setObjectName("dcGhost")
        self.btn_fill.setToolTip("Everything arrived as ordered: copy the ordered quantity and price into the result")
        self.btn_confirm = QPushButton(self.w["confirm"])
        self.btn_confirm.setObjectName("msPay")
        self.btn_correct = QPushButton("Review / correct a line…")
        self.btn_correct.setObjectName("dcGhost")
        self.btn_correct.setToolTip("Goods found damaged or short after delivery, a wrong count or a price dispute")
        self.btn_revert = QPushButton("Revert to draft")
        self.btn_revert.setObjectName("dcGhost")
        self.btn_cancel = QPushButton("Cancel order")
        self.btn_cancel.setObjectName("dcGhost")
        for b in (self.btn_add, self.btn_remove, self.btn_fill, self.btn_go, self.btn_confirm, self.btn_correct):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_revert)
        bar.addWidget(self.btn_cancel)
        root.addLayout(bar)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setVisible(False)
        root.addWidget(self.msg)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.slot_lines = QWidget()
        self.slot_lines.setLayout(QVBoxLayout())
        self.slot_lines.layout().setContentsMargins(0, 0, 0, 0)
        self.slot_pay = QWidget()
        self.slot_pay.setLayout(QVBoxLayout())
        self.slot_pay.layout().setContentsMargins(0, 0, 0, 0)
        self.slot_money = QWidget()
        self.slot_money.setLayout(QVBoxLayout())
        self.slot_money.layout().setContentsMargins(0, 0, 0, 0)
        self.slot_exp = QWidget()
        self.slot_exp.setLayout(QVBoxLayout())
        self.slot_exp.layout().setContentsMargins(0, 0, 0, 0)
        self.slot_hist = QWidget()
        self.slot_hist.setLayout(QVBoxLayout())
        self.slot_hist.layout().setContentsMargins(0, 0, 0, 0)
        for s in (self.slot_lines, self.slot_pay, self.slot_money, self.slot_exp, self.slot_hist):
            self.stack.addWidget(s)

        tabs = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setObjectName("msSheets")
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.ElideNone)
        for text, tip in (("Products", "The products on this order, with their quantity and price"),
                          ("Payments", "Money paid towards this order's price"),
                          ("Other money", "Cash that moved between us outside the order price (advances, loans, refunds)"),
                          ("Expenses", "What we spent on this order (driver, fuel, food ...) and what they pay back"),
                          ("History", "Everything that happened to this order, in order")):
            self.tabs.addTab(text)
            self.tabs.setTabToolTip(self.tabs.count() - 1, tip)
        tabs.addWidget(self.tabs)
        tabs.addStretch(1)
        root.addLayout(tabs)

        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.btn_add.clicked.connect(lambda: self.grid.add_row())
        self.btn_remove.clicked.connect(lambda: self.grid.remove())
        self.btn_go.clicked.connect(self._go)
        self.btn_next.clicked.connect(self._next_step)
        self.btn_correct.clicked.connect(self._correct)
        self.btn_fill.clicked.connect(self._fill)
        self.btn_confirm.clicked.connect(self._confirm)
        self.btn_revert.clicked.connect(self._revert)
        self.btn_cancel.clicked.connect(self._cancel)
        self.chk_expected.toggled.connect(self._header_changed)
        self.expected.dateChanged.connect(lambda *_: self._header_changed())
        self.notes.editingFinished.connect(self._header_changed)
        self.grid: SheetGrid = None
        self.pay_grid: SheetGrid = None
        self.money_grid: SheetGrid = None
        self.exp_grid: SheetGrid = None

    # ================================================================== state
    @property
    def stage(self) -> str:
        return self.d["status"] if self.d else "new"

    @property
    def editable_draft(self) -> bool:
        return self.is_admin and self.stage in ("new", "draft")

    def say(self, text: str, bad: bool = True) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        self.msg.setStyleSheet(f"color:{'#a23b34' if bad else '#3f6b4a'}; font-size:12.5px; font-weight:600;")

    def reload(self) -> None:
        if self.order_id:
            self.d = OrderSheetService.detail(self.kind, self.order_id)
            self.lines = [dict(l) for l in self.d["lines"]]
        else:
            self.d = None
        self._render_head()
        self._render_info()
        self._render_buttons()
        self._build_lines_grid()
        self._build_pay_grid()
        self._build_money_grid()
        self._build_exp_grid()
        self._build_history()

    # ================================================================== header
    def _render_head(self) -> None:
        while self.head_slot.count():
            wd = self.head_slot.takeAt(0).widget()
            if wd is not None:
                wd.hide()
                wd.setParent(None)
                wd.deleteLater()
        self.head_slot.addWidget(avatar(self.partner["name"], 44))
        title = (self.d["order_no"] if self.d else f"New {'Sales' if self.kind == 'sales' else 'Purchase'} Order")
        box = QVBoxLayout()
        box.setSpacing(0)
        name = QLabel(f"{title}  -  {self.partner['name']}")
        name.setStyleSheet("font-size:19px; font-weight:800; color:#1c2333;")
        sub = QLabel("  -  ".join(x for x in (self.partner.get("phone"), self.partner.get("address")) if x)
                     or self.w["plain"])
        sub.setObjectName("dcSub")
        box.addWidget(name)
        box.addWidget(sub)
        holder = QWidget()
        holder.setLayout(box)
        self.head_slot.addWidget(holder)
        st = self.stage
        c = chip(STATUS_TEXT.get(st, "New"), STATUS_TONE.get(st, "amber"))
        c.setFixedHeight(22)
        self.head_slot.addWidget(c, 0, Qt.AlignVCenter)

        done = {"new": 0, "draft": 0, "in_transit": 1, "confirmed": 2, "cancelled": -1}[st]
        if st == "confirmed" and self.d and self.d["pay_status"] == "paid":
            done = 3
        for i, lab in enumerate(self.step_labels):
            cur = i == done
            past = i < done
            lab.setText(("✓ " if past else "") + STAGES[self.kind][i])
            lab.setStyleSheet(
                "padding:4px 12px; border-radius:11px; font-size:11.5px; font-weight:700;"
                + ("background:#1c2333; color:#ffffff;" if cur else
                   "background:#e3ecdf; color:#3f6b4a;" if past else
                   "background:#ece8df; color:#8a8578;") + ("opacity:0.4;" if st == "cancelled" else ""))
        d = self.d
        value = d["total"] if d else estimate(self.kind, self.lines)
        self.k_value.val.setText(money(value))
        self.k_value.cap.setText("Order value" + (" - not final until delivery is confirmed" if (not d or d["estimated"])
                                                  else " (final)"))
        self.k_paid.val.setText(money(d["paid"] if d else 0))
        if d and d["balance"] is not None and d["overpaid"] > 0.005:
            self.k_balance.val.setText(money(d["overpaid"]))
            self.k_balance.cap.setText("Overpaid - refund or void a payment")
        elif d and d["balance"] is not None:
            self.k_balance.val.setText(money(d["balance"]))
            self.k_balance.cap.setText("Still to pay" if self.kind == "purchase" else "Still to receive")
        else:
            self.k_balance.val.setText("-")
            self.k_balance.cap.setText(("Still to pay" if self.kind == "purchase" else "Still to receive")
                                       + " - known once delivery is confirmed")
        self.k_lines.val.setText(str(len(self.lines)))

    def _render_info(self) -> None:
        d = self.d
        editable = self.editable_draft
        self.notes.blockSignals(True)
        self.expected.blockSignals(True)
        self.chk_expected.blockSignals(True)
        self.notes.setText(d["notes"] if d else self.notes.text())
        exp = d["expected"] if d else None
        if exp:
            self.expected.setDate(QDate(exp.year, exp.month, exp.day))
        self.chk_expected.setChecked(bool(exp) if d else self.chk_expected.isChecked())
        for wd in (self.notes, self.expected, self.chk_expected):
            wd.setEnabled(editable)
        self.notes.blockSignals(False)
        self.expected.blockSignals(False)
        self.chk_expected.blockSignals(False)
        self.expected.setVisible(editable or bool(exp))
        self.lbl_expected.setVisible(editable or bool(exp))
        self.chk_expected.setVisible(editable)
        meta = []
        if d and d["driver"]:
            dr = d["driver"]
            meta.append("Driver: " + " | ".join(x for x in (dr["name"], dr.get("phone"), dr.get("vehicle_number")) if x))
        self.lbl_meta.setText("   ".join(meta))

    def _render_buttons(self) -> None:
        st, adm = self.stage, self.is_admin
        draft = st in ("new", "draft")
        self.btn_add.setVisible(adm and draft)
        self.btn_remove.setVisible(adm and draft)
        self.btn_go.setVisible(False)                 # the big button beside the steps does this - one place only
        self.btn_fill.setVisible(st == "in_transit")
        self.btn_confirm.setVisible(False)
        self.btn_revert.setVisible(adm and st == "in_transit")
        self.btn_cancel.setVisible(adm and st == "draft")
        self.btn_correct.setVisible(adm and st == "confirmed")
        self._render_next()

    # ================================================================== what happens next
    def _render_next(self) -> None:
        """One plain sentence saying where the order is and the single thing to do next."""
        st, adm, w = self.stage, self.is_admin, self.w
        d = self.d
        if st == "new":
            text, hint, show = "", "Step 1 of 4 - Add the products below. The order is saved as a draft as soon as the first line is complete.", False
        elif st == "draft":
            show = adm
            text = f"{w['go']}  →"
            hint = ("Step 1 of 4 - Draft. Add or change the products below; a draft can stay as long as you like and "
                    "moves no stock. When the goods "
                    + ("leave the warehouse" if self.kind == "sales" else "are on the road")
                    + f", press {w['go']}.")
        elif st == "in_transit":
            show, text = True, f"{w['confirm']}  →"
            hint = ("Step 2 of 4 - On the road. When the goods arrive, type below how much was "
                    + ("received by the company" if self.kind == "sales" else "received")
                    + ", how much was rejected and the price (or press Fill from order), then press "
                    + w["confirm"] + ".")
        elif st == "confirmed":
            owing = d and d["balance"] is not None and d["balance"] > 0.005
            show, text = bool(owing), "Record a payment  →"
            over = d["overpaid"] if d else 0
            if owing:
                hint = "Step 3 of 4 - Delivered and confirmed. Record the payment in the Payments tab. "
            elif over > 0.005:
                hint = f"Overpaid by {money(over)} - void or refund in the Payments tab. "
            else:
                hint = "Step 4 of 4 - Fully paid. "
            hint += ("Something wrong after delivery (damaged, short, wrong count)? Use Review / correct a line."
                     if adm else "")
        else:
            show, text, hint = False, "", "This order was cancelled."
        self.btn_next.setVisible(show)
        self.btn_next.setText(text)
        self.lbl_next.setText(hint)

    def _next_step(self) -> None:
        st = self.stage
        if st == "draft":
            self._go()
        elif st == "in_transit":
            self._confirm()
        elif st == "confirmed":
            self.tabs.setCurrentIndex(1)

    def _correct(self) -> None:
        line = None
        row = self.grid.table.currentRow() if self.grid else -1
        if 0 <= row < len(self.lines):
            line = self.lines[row]
        if line is None:
            self.say("Click the line you want to review first.")
            return
        from apps.product_management_app.features.orders.dialogs.correction_dialog import CorrectionDialog
        dlg = CorrectionDialog(self.kind, self.d, line, self)
        if dlg.exec_() == dlg.Accepted:
            self.say("Correction applied - it is in the History tab.", bad=False)
            self._refresh()
            self.tabs.setCurrentIndex(0)

    # ================================================================== lines grid
    def _line_cells(self, l: Dict[str, Any]) -> Dict[str, Any]:
        label = next((k for k, v in self.labels.items() if v == l["product_id"]), l["name"])
        return {"product": label, "bags": l["bags"] or "", "kg": l["kg"] or "", "price": l["price"] or "",
                "unit": l["qty_unit"] if "qty_unit" in l else ("bags" if l["type"] == "fertilizer" else "kg"),
                "ordered": l["bags"] if l["type"] == "fertilizer" else l["kg"], "oprice": l["price"],
                "rcv": "" if l.get("rcv_qty") is None else l["rcv_qty"], "rej": "" if l.get("rej_qty") is None else l["rej_qty"],
                "rprice": "" if l.get("rcv_price") is None else l["rcv_price"]}

    def _line_date(self) -> str:
        """Lines carry the day the order was made (today for one not saved yet)."""
        from datetime import datetime
        d = (self.d or {}).get("created_at") if isinstance(getattr(self, "d", None), dict) else None
        return (d or datetime.now()).strftime("%d/%m/%Y")

    @staticmethod
    def _line_total(l: Dict[str, Any]) -> float:
        return l["price"] * (l["bags"] if l["type"] == "fertilizer" else l["kg"])

    def _build_lines_grid(self) -> None:
        st = self.stage
        w = self.w
        if st in ("new", "draft"):
            cols = [
                {"key": "product", "label": "Product", "type": "choice", "editable": True,
                 "options": list(self.labels), "width": 260, "stretch": True,
                 "readonly": not self.is_admin},
                {"key": "bags", "label": "Bags", "type": "number", "width": 100, "readonly": not self.is_admin},
                {"key": "kg", "label": "Weight (kg)\n(crops)", "type": "number", "width": 130,
                 "readonly": not self.is_admin},
                {"key": "price", "label": "Price (₦)\nper kg or bag", "type": "money", "width": 150,
                 "readonly": not self.is_admin},
            ]
            computed = [{"key": "date", "label": "Date", "width": 110},
                        {"key": "total", "label": "Line total\n(worked out)", "width": 160}]
            kw = dict(first_edit_key="product", placeholder_key="product", defaults={},
                      ready=self._ready_line, on_add=self._add_line, on_edit=self._edit_line,
                      on_remove=self._remove_line, allow_add=self.is_admin,
                      hint="Type in the last row: the product (pick it or type its name), bags, kg (crops only) and the "
                           "price - the row saves itself. Click a cell to change it; right-click a row to remove it.")
        else:
            lock = st != "in_transit"
            cols = [
                {"key": "product", "label": "Product", "type": "text", "width": 260, "stretch": True, "readonly": True},
                {"key": "unit", "label": "Unit", "type": "text", "width": 60, "readonly": True},
                {"key": "ordered", "label": f"{w['ordered']}\n(kg / bags)", "type": "number", "width": 110, "readonly": True},
                {"key": "oprice", "label": "Order price (₦)", "type": "money", "width": 130, "readonly": True},
                {"key": "rcv", "label": f"{w['rcv']}\n(kg / bags)", "type": "number", "width": 160, "readonly": lock},
                {"key": "rej", "label": "Rejected\n(kg / bags)", "type": "number", "width": 100, "readonly": lock},
                {"key": "rprice", "label": w["price"], "type": "money", "width": 140, "readonly": lock},
            ]
            computed = [{"key": "date", "label": "Date", "width": 110},
                        {"key": "acc", "label": "Accepted\n(worked out)", "width": 110},
                        {"key": "amount", "label": "Line amount\n(worked out)", "width": 150}]
            kw = dict(first_edit_key="rcv", ready=None, on_add=None, on_edit=self._edit_result, allow_add=False,
                      hint=("For each product type how much was " + ("received by the company" if self.kind == "sales" else "received")
                            + ", how much was rejected and the price. What you type is kept; Accepted and the amount "
                            "are worked out for you."
                            if not lock else "Final figures. Accepted = received - rejected; amount = accepted x price."))
        rows = []
        for i, l in enumerate(self.lines):
            cells = self._line_cells(l)
            acc = amount = ""
            if l.get("rcv_qty") is not None:
                a = l["rcv_qty"] - (l.get("rej_qty") or 0)
                acc = f"{a:,.2f}".rstrip("0").rstrip(".")
                if l.get("rcv_price") is not None:
                    amount = money(a * l["rcv_price"])
            comp = {"date": self._line_date(), "total": money(self._line_total(l)), "acc": acc, "amount": amount}
            rows.append({"id": i, "cells": cells, "computed": comp,
                         "tip": ""})
        totals: Dict[str, str] = {}
        if st in ("new", "draft"):
            totals = {"total": money(estimate(self.kind, self.lines))}
        else:
            amt = sum((l["rcv_qty"] - (l.get("rej_qty") or 0)) * l["rcv_price"] for l in self.lines
                      if l.get("rcv_qty") is not None and l.get("rcv_price") is not None)
            totals = {"amount": money(amt)}
        grid = SheetGrid(columns=cols, computed=computed, reload=self._refresh, notify=self.say, **kw)
        grid.set_rows(rows, totals)
        self._swap(self.slot_lines, grid)
        self.grid = grid

    def _swap(self, slot: QWidget, grid: QWidget) -> None:
        lay = slot.layout()
        while lay.count():
            old = lay.takeAt(0).widget()
            if old is not None:
                old.hide()
                old.setParent(None)
                old.deleteLater()
        lay.addWidget(grid)

    def _refresh(self) -> None:
        self.reload()
        self.changed.emit()

    # ---------------------------------------------------------------- draft editing
    def _find_product_id(self, text: str):
        """The product a cell's text means: the exact list entry, or - typed by hand - its name / SKU in any
        capitals, or a part of it that only one product has ('paddy' -> 'Paddy Rice  (CROP-PADDY)')."""
        t = (text or "").strip()
        if not t:
            return None
        if t in self.labels:
            return self.labels[t]
        low = t.lower()
        for lab, pid in self.labels.items():
            p = self.products[pid]
            if low in (lab.strip().lower(), str(p["name"]).strip().lower(), str(p.get("sku", "")).strip().lower()):
                return pid
        part = {pid for lab, pid in self.labels.items() if low in lab.lower()}
        return next(iter(part)) if len(part) == 1 else None

    def _product(self, label: str) -> Dict[str, Any]:
        pid = self._find_product_id(label)
        if pid is None:
            raise ValueError("Pick the product from the list (or type its name).")
        return self.products[pid]

    def _ready_line(self, v: Dict[str, Any]) -> str:
        if not str(v.get("product", "")).strip():
            return "Now pick the product to save the line."
        try:
            p = self._product(v["product"])
            bags, kg = _num(v.get("bags")), _num(v.get("kg"))
            price = _num(v.get("price"))
        except ValueError as e:
            return str(e)
        if not bags or bags <= 0:
            return "Now type the bags."
        if p["type"] == "crops" and (not kg or kg <= 0):
            return "Crops need the weight in kg as well."
        if price is None or price <= 0:
            usual = f" (usual: {money(p['price'])})" if p["price"] > 0 else ""
            return f"Now type the price{usual} to save the line."
        return ""

    def _new_line(self, v: Dict[str, Any]) -> Dict[str, Any]:
        p = self._product(v["product"])
        return {"product_id": p["id"], "name": p["name"], "type": p["type"], "bags": _num(v.get("bags")) or 0.0,
                "kg": 0.0 if p["type"] == "fertilizer" else (_num(v.get("kg")) or 0.0),
                "price": _num(v.get("price")) or 0.0, "qty_unit": "bags" if p["type"] == "fertilizer" else "kg"}

    def _persist_draft(self, lines: List[Dict[str, Any]]) -> None:
        notes = self.notes.text().strip()
        expected = self.expected.date().toPython() if self.chk_expected.isChecked() else None
        if self.order_id is None:
            self.order_id = OrderSheetService.create(self.kind, self.partner_id, lines, notes, expected)
            self.opened.emit(self.order_id)
        else:
            OrderSheetService.save_draft(self.kind, self.order_id, lines, notes, expected)

    def _add_line(self, values: Dict[str, Any]) -> None:
        self._persist_draft(self.lines + [self._new_line(values)])

    def _edit_line(self, row_id: int, key: str, raw: str) -> None:
        lines = [dict(l) for l in self.lines]
        l = lines[row_id]
        if key == "product":
            p = self._product(raw)
            l.update(product_id=p["id"], name=p["name"], type=p["type"])
            if p["type"] == "fertilizer":
                l["kg"] = 0.0
        elif key in ("bags", "kg", "price"):
            v = _num(raw)
            l[key] = v or 0.0
        self._persist_draft(lines)

    def _remove_line(self, row_id: int, reason: str) -> None:
        lines = [dict(l) for i, l in enumerate(self.lines) if i != row_id]
        if not lines:
            raise ValueError("A draft needs at least one line - use Cancel order if you do not want it.")
        self._persist_draft(lines)

    def _header_changed(self, *_a) -> None:
        if self.order_id is not None and self.stage == "draft" and self.lines:
            try:
                self._persist_draft([dict(l) for l in self.lines])
                self.say("")
            except (ValueError, PermissionDenied) as e:
                self.say(str(e))

    # ---------------------------------------------------------------- delivery result
    def _edit_result(self, row_id: int, key: str, raw: str) -> None:
        l = self.lines[row_id]
        res = {"id": l["id"], "type": l["type"], "rcv_qty": None, "rej_qty": None, "rcv_price": None}
        value = _num(raw)
        if key == "rcv_price" or key == "rprice":
            res["rcv_price"] = value
        else:
            rcv = value if key == "rcv" else l.get("rcv_qty")
            rej = value if key == "rej" else (l.get("rej_qty") or 0.0)
            if rcv is None:
                raise ValueError("Type how much was received first.")
            if (rej or 0) > rcv + 1e-9:
                raise ValueError("Rejected cannot be more than what was received.")
            if (rcv or 0) <= 0:
                raise ValueError("The received quantity must be above zero.")
            res.update(rcv_qty=rcv, rej_qty=rej or 0.0)
        OrderSheetService.save_result(self.kind, self.order_id, [res])

    def _fill(self) -> None:
        try:
            results = [{"id": l["id"], "type": l["type"],
                        "rcv_qty": l["bags"] if l["type"] == "fertilizer" else l["kg"], "rej_qty": 0.0,
                        "rcv_price": l["price"]} for l in self.lines]
            OrderSheetService.save_result(self.kind, self.order_id, results)
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self._refresh()

    def _results(self) -> List[Dict[str, Any]]:
        return [{"id": l["id"], "type": l["type"], "rcv_qty": l.get("rcv_qty"), "rej_qty": l.get("rej_qty") or 0.0,
                 "rcv_price": l.get("rcv_price")} for l in self.lines]

    def _confirm(self) -> None:
        missing = [l["name"] for l in self.lines if l.get("rcv_qty") is None or l.get("rcv_price") is None]
        if missing:
            self.say("Still to enter (received quantity and price): " + ", ".join(missing))
            return
        total = sum((l["rcv_qty"] - (l.get("rej_qty") or 0)) * l["rcv_price"] for l in self.lines)
        what = ("Stock for any rejected goods goes back on the shelf." if self.kind == "sales"
                else "Accepted goods are added to stock.")
        if QMessageBox.question(self, "Confirm", f"Finalise {self.d['order_no']} for {money(total)}?\n\n{what}\n"
                                "This cannot be undone.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            OrderSheetService.confirm(self.kind, self.order_id, self._results())
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("")
        self._refresh()
        self.tabs.setCurrentIndex(1)

    # ---------------------------------------------------------------- stage actions
    def _go(self) -> None:
        from apps.product_management_app.features.sales_orders.dialogs.driver_picker_dialog import DriverPickerDialog
        dlg = DriverPickerDialog(parent=self, skip_label=f"{self.w['go']} without driver")
        if dlg.exec_() != dlg.Accepted:
            return
        what = ("This takes the goods off the shelf and puts the order on the road." if self.kind == "sales"
                else "This marks the order as on the road (no stock moves until it is received).")
        if QMessageBox.question(self, self.w["go"], f"{self.w['go']} {self.d['order_no']}?\n\n{what}",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        try:
            OrderSheetService.dispatch(self.kind, self.order_id, dlg.selected_driver_id)
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("")
        self._refresh()

    def _revert(self) -> None:
        if QMessageBox.question(self, "Revert to draft", "Take this order back to a draft? "
                                + ("The stock taken at dispatch is put back. " if self.kind == "sales" else "")
                                + "Anything entered for the delivery is cleared.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            OrderSheetService.revert(self.kind, self.order_id)
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("")
        self._refresh()

    def _cancel(self) -> None:
        reason, ok = QInputDialog.getText(self, "Cancel order", "Cancel this draft order? Why? (optional)")
        if not ok:
            return
        try:
            OrderSheetService.cancel(self.kind, self.order_id, reason.strip())
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("")
        self._refresh()

    # ================================================================== payments
    def _build_pay_grid(self) -> None:
        d = self.d
        can_pay = bool(d) and d["status"] != "cancelled"
        cols = [
            {"key": "date", "label": "Date", "type": "date", "width": 110, "readonly": True},
            {"key": "amount", "label": "Amount (₦)", "type": "money", "width": 160, "readonly": False},
            {"key": "method", "label": "How", "type": "choice", "options": METHOD_OPTIONS, "width": 110},
            {"key": "note", "label": "Note", "type": "text", "width": 300, "stretch": True},
            {"key": "by", "label": "By", "type": "text", "width": 120, "readonly": True},
        ]
        rows = []
        for p in reversed(d["payments"] if d else []):          # oldest first, as written
            voided = p["status"] == "voided"
            rows.append({"id": p["id"], "removed": voided, "locked": True,
                         "cells": {"date": p["created_at"].date().isoformat() if p["created_at"] else "",
                                   "amount": p["amount"], "method": (p["method"] or "").title(), "note": p["notes"],
                                   "by": p["created_by"]},
                         "tip": ("Voided: " + p.get("void_reason", "")) if voided else ""})
        live = sum(p["amount"] for p in (d["payments"] if d else []) if p["status"] != "voided")
        hint = ("Type each payment in the last row: the amount and how it was paid. " +
                ("Paid before delivery? It is kept as an advance and counts once the final price is known. "
                 if d and d["status"] != "confirmed" else "A payment cannot be more than what is still owed. ") +
                "Made a mistake? Right-click the row to cancel it (give a reason).") if can_pay else (
            "Save the order first - payments can be recorded once it exists." if not d else "This order was cancelled.")
        grid = SheetGrid(columns=cols, first_edit_key="amount", placeholder_key="amount",
                         defaults={"date": date.today().isoformat(), "method": "Cash"},
                         ready=self._ready_pay, on_add=self._add_pay, on_edit=None, on_remove=self._void_pay,
                         reload=self._refresh, hint=hint, allow_add=can_pay, notify=self.say)
        grid.set_rows(rows, {"amount": money(live)})
        self._swap(self.slot_pay, grid)
        self.pay_grid = grid

    def _ready_pay(self, v) -> str:
        try:
            a = _num(v.get("amount"))
        except ValueError:
            return "The amount must be a number."
        return "" if a and a > 0 else "Now type the amount."

    def _add_pay(self, v) -> None:
        OrderSheetService.pay(self.kind, self.order_id, _num(v.get("amount")), (v.get("method") or "Cash").lower(),
                              v.get("note", ""))

    def _void_pay(self, pay_id: int, reason: str) -> None:
        OrderSheetService.void_payment(pay_id, reason)

    # ================================================================== money given / received
    def _tab_with_buttons(self, grid: SheetGrid, *buttons: QPushButton) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if buttons:
            row = QHBoxLayout()
            for b in buttons:
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
        grid.setMinimumHeight(260)                # always usable; the screen scrolls if the window is short
        lay.addWidget(grid, 1)
        return holder

    def _build_money_grid(self) -> None:
        d = self.d
        can = bool(d) and d["status"] != "cancelled"
        cols = [
            {"key": "date", "label": "Date", "type": "date", "width": 110, "readonly": True},
            {"key": "direction", "label": "Which way", "type": "choice", "options": MONEY_WAYS, "width": 150},
            {"key": "amount", "label": "Amount (₦)", "type": "money", "width": 150},
            {"key": "method", "label": "How", "type": "choice", "options": METHOD_OPTIONS, "width": 110},
            {"key": "note", "label": "Note", "type": "text", "width": 300, "stretch": True},
            {"key": "by", "label": "By", "type": "text", "width": 110, "readonly": True},
        ]
        rows = []
        entries = OrderMoneyService.list_money(self.kind, order_id=self.order_id) if d else []
        for m in entries:
            voided = m["status"] == "voided"
            rows.append({"id": m["id"], "removed": voided, "locked": True,
                         "cells": {"date": m["date"].isoformat() if m["date"] else "",
                                   "direction": MONEY_WAYS[0] if m["direction"] == "in" else MONEY_WAYS[1],
                                   "amount": m["amount"], "method": m["method"].title(), "note": m["note"],
                                   "by": m["by"]},
                         "computed": {"state": "Applied to the order" if m["status"] == "applied" else ""},
                         "tip": ("Voided: " + m["void_reason"]) if voided else ""})
        live = [m for m in entries if m["status"] != "voided"]
        net = sum(m["amount"] if m["direction"] == "in" else -m["amount"] for m in live)
        hint = ("Money that moved between us but is NOT the order price, e.g. an advance or a loan. Type it in the last "
                "row. To count it as a payment for this order, click its row and press the button below ("
                + ("money they gave us" if self.kind == "sales" else "money we gave them") + ").") if can else (
            "Save the order first." if not d else "This order was cancelled.")
        grid = SheetGrid(columns=cols, computed=[{"key": "state", "label": "Status", "width": 170}],
                         first_edit_key="amount", placeholder_key="amount",
                         defaults={"date": date.today().isoformat(), "direction": MONEY_WAYS[0], "method": "Cash"},
                         ready=self._ready_money, on_add=self._add_money, on_edit=None, on_remove=self._void_money,
                         reload=self._refresh, hint=hint, allow_add=can, notify=self.say)
        grid.set_rows(rows, {"amount": f"net {money(net)}"})
        self.btn_apply_money = QPushButton("Use the selected row as a payment for this order")
        self.btn_apply_money.setVisible(can)
        self.btn_apply_money.clicked.connect(self._apply_money)
        self._swap(self.slot_money, self._tab_with_buttons(grid, self.btn_apply_money))
        self.money_grid = grid

    def _ready_money(self, v) -> str:
        try:
            a = _num(v.get("amount"))
        except ValueError:
            return "The amount must be a number."
        return "" if a and a > 0 else "Now type the amount."

    def _add_money(self, v) -> None:
        way = "in" if (v.get("direction") or MONEY_WAYS[0]) == MONEY_WAYS[0] else "out"
        OrderMoneyService.add_money(self.kind, self.partner_id, way, _num(v.get("amount")), order_id=self.order_id,
                                    method=(v.get("method") or "Cash").lower(), note=v.get("note", ""))

    def _void_money(self, money_id: int, reason: str) -> None:
        OrderMoneyService.void_money(money_id, reason)

    def _apply_money(self) -> None:
        row = None
        if self.money_grid:
            tbl = self.money_grid.table
            r = tbl.currentRow()
            if r < 0 and tbl.selectedItems():
                r = tbl.selectedItems()[0].row()
            row = self.money_grid.rows[r] if 0 <= r < len(self.money_grid.rows) else None
        if row is None or row.get("removed"):
            self.say("Click the money row you want to apply first.")
            return
        try:
            OrderMoneyService.apply_to_order(row["id"], self.order_id)
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("Applied - it is now a payment on this order.", bad=False)
        self._refresh()

    # ================================================================== expenses for this order
    def _build_exp_grid(self) -> None:
        d = self.d
        can = bool(d) and d["status"] != "cancelled"
        default_charge = EXPENSE_CHARGE[0] if self.kind == "purchase" else EXPENSE_CHARGE[1]
        cols = [
            {"key": "date", "label": "Date", "type": "date", "width": 110, "readonly": True},
            {"key": "category", "label": "What for", "type": "choice", "editable": True,
             "options": EXPENSE_CATEGORIES, "width": 150},
            {"key": "description", "label": "Details", "type": "text", "width": 240, "stretch": True},
            {"key": "amount", "label": "Amount (₦)", "type": "money", "width": 140},
            {"key": "paid_to", "label": "Paid to", "type": "text", "width": 130},
            {"key": "charge", "label": "Charge", "type": "choice", "options": EXPENSE_CHARGE, "width": 160},
            {"key": "method", "label": "How", "type": "choice", "options": METHOD_OPTIONS, "width": 100},
            {"key": "by", "label": "By", "type": "text", "width": 100, "readonly": True},
        ]
        rows = []
        exps = OrderMoneyService.list_expenses(self.kind, order_id=self.order_id) if d else []
        for e in exps:
            voided = e["status"] == "voided"
            rows.append({"id": e["id"], "removed": voided, "locked": True,
                         "cells": {"date": e["date"].isoformat() if e["date"] else "", "category": e["category"],
                                   "description": e["description"], "amount": e["amount"], "paid_to": e["paid_to"],
                                   "charge": EXPENSE_CHARGE[0] if e["recoverable"] else EXPENSE_CHARGE[1],
                                   "method": e["method"].title(), "by": e["by"]},
                         "computed": {"back": money(e["recovered"]) if e["recoverable"] and not voided else "",
                                      "left": (money(e["left"]) if e["recoverable"] and not voided else
                                               ("" if voided else "our cost"))},
                         "tip": ("Voided: " + e["void_reason"]) if voided else e["no"]})
        live = [e for e in exps if e["status"] != "voided"]
        totals = {"amount": money(sum(e["amount"] for e in live)),
                  "back": money(sum(e["recovered"] for e in live)), "left": money(sum(e["left"] for e in live))}
        hint = ("Things we paid for on this order (driver, fuel, food ...). “They pay back” = the other side will "
                "return it to us; “Our cost” = we carry it. When they pay some back, press Record repayment.") if can else ("Save the order first." if not d else "This order was cancelled.")
        grid = SheetGrid(columns=cols, computed=[{"key": "back", "label": "Paid back", "width": 120},
                                                 {"key": "left", "label": "Still owed", "width": 120}],
                         first_edit_key="category", placeholder_key="category",
                         defaults={"date": date.today().isoformat(), "category": EXPENSE_CATEGORIES[0],
                                   "charge": default_charge, "method": "Cash"},
                         ready=self._ready_expense, on_add=self._add_expense, on_edit=None,
                         on_remove=self._void_expense, reload=self._refresh, hint=hint, allow_add=can,
                         notify=self.say)
        grid.set_rows(rows, totals)
        self.btn_repay = QPushButton("They paid some back…")
        self.btn_repay.setVisible(can)
        self.btn_repay.clicked.connect(self._repay)
        self._swap(self.slot_exp, self._tab_with_buttons(grid, self.btn_repay))
        self.exp_grid = grid

    def _ready_expense(self, v) -> str:
        try:
            a = _num(v.get("amount"))
        except ValueError:
            return "The amount must be a number."
        return "" if a and a > 0 else "Now type the amount."

    def _add_expense(self, v) -> None:
        default_charge = EXPENSE_CHARGE[0] if self.kind == "purchase" else EXPENSE_CHARGE[1]
        OrderMoneyService.add_expense(
            self.kind, self.order_id, v.get("category") or EXPENSE_CATEGORIES[0], _num(v.get("amount")),
            v.get("description", ""), recoverable=(v.get("charge") or default_charge) == EXPENSE_CHARGE[0],
            paid_to=v.get("paid_to", ""), method=(v.get("method") or "Cash").lower())

    def _void_expense(self, expense_id: int, reason: str) -> None:
        OrderMoneyService.void_expense(expense_id, reason)

    def _repay(self) -> None:
        from apps.product_management_app.features.orders.dialogs.repayment_dialog import RepaymentDialog
        owed = sum(e["left"] for e in OrderMoneyService.list_expenses(self.kind, order_id=self.order_id)
                   if e["status"] == "active")
        if owed <= 0.005:
            self.say("Nothing is owed back on this order.", bad=False)
            return
        dlg = RepaymentDialog(owed, self, who=self.partner.get("name", "They"))
        if dlg.exec_() != dlg.Accepted or not dlg.values:
            return
        try:
            OrderMoneyService.record_recovery(dlg.values["amount"], kind=self.kind, order_id=self.order_id,
                                              method=dlg.values["method"], note=dlg.values["note"])
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("Repayment recorded.", bad=False)
        self._refresh()

    # ================================================================== history
    def _build_history(self) -> None:
        d = self.d
        events = []
        for e in (d["timeline"] if d else []):
            events.append((e["at"], "Status", e["event"], e["by"], ""))
        for p in (d["payments"] if d else []):
            note = f"Payment {money(p['amount'])} ({(p['method'] or '').title()})"
            events.append((p["created_at"], "Payment", note + (" - VOIDED" if p["status"] == "voided" else ""),
                           p["created_by"], p["notes"]))
        for r in (d["reviews"] if d else []):
            what = r["text"]
            if r["correction"]:
                what = f"{r['text']}   [total {money(r['total_before'])} → {money(r['total_after'])}" + \
                       (", stock adjusted" if r["stock_moved"] else ", stock not moved") + "]"
            events.append((r["at"], ("Correction: " if r["correction"] else "") + r["category"], what, r["by"], ""))
        events.sort(key=lambda e: (e[0] is None, e[0]))
        cols = [{"key": "at", "label": "When", "type": "text", "width": 140, "readonly": True},
                {"key": "category", "label": "Type", "type": "choice", "options": REVIEW_CATEGORIES, "width": 190},
                {"key": "text", "label": "What happened / note", "type": "text", "width": 420, "stretch": True},
                {"key": "by", "label": "By", "type": "text", "width": 120, "readonly": True},
                {"key": "extra", "label": "Note", "type": "text", "width": 160, "readonly": True}]
        rows = [{"id": i, "locked": True,
                 "cells": {"at": e[0].strftime("%d/%m/%Y %H:%M") if e[0] else "", "category": e[1], "text": e[2],
                           "by": e[3], "extra": e[4]}} for i, e in enumerate(events)]
        can_note = bool(d)
        grid = SheetGrid(columns=cols, first_edit_key="text", placeholder_key="text", defaults={"category": "Note"},
                         ready=self._ready_note, on_add=self._add_note, allow_add=can_note, notify=self.say,
                         reload=self._refresh,
                         hint=("Everything that has happened to this order, in order. Type a review note in the last "
                               "row (a delay, damage on the road, a call with the driver ...); it is kept for good."
                               if can_note else "Save the order first - notes can be added once it exists."))
        grid.set_rows(rows, {"text": f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}"})
        self._swap(self.slot_hist, grid)

    def _ready_note(self, v) -> str:
        return "" if str(v.get("text", "")).strip() else "Now write the note to save it."

    def _add_note(self, v) -> None:
        OrderSheetService.add_review(self.kind, self.order_id, v.get("category") or "Note", v.get("text", ""))
