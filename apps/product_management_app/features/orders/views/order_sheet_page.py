# apps/product_management_app/features/orders/views/order_sheet_page.py
"""
An order as a sheet - Sales Order or Purchase Order, one screen for the whole life of the order:

    Draft  ->  On the road  ->  Delivered & confirmed  ->  Paid

    Order lines tab   type the lines into the grid (draft); enter what the other side actually received,
                      rejected and the price (on the road); read the final figures (confirmed)
    Payments tab      type a payment into the last row; remove = void (with a reason)
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

from database.services.order_sheet_service import OrderSheetService, estimate
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import avatar, chip, money
from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.debt_center.views.sheet_grid import SheetGrid

STAGES = {
    "sales": ["Draft", "Dispatched", "Delivered & confirmed", "Paid"],
    "purchase": ["Draft", "In transit", "Received & confirmed", "Paid"],
}
STATUS_TEXT = {"draft": "Draft", "in_transit": "On the road", "confirmed": "Confirmed", "cancelled": "Cancelled"}
STATUS_TONE = {"draft": "grey", "in_transit": "blue", "confirmed": "green", "cancelled": "red"}
METHOD_OPTIONS = ["Cash", "Transfer", "POS", "Cheque"]
WORDS = {
    "sales": {"partner": "Company", "go": "Dispatch", "go_done": "Dispatched", "rcv": "Company received",
              "price": "Company price (₦)", "confirm": "Confirm delivery", "ordered": "Dispatched"},
    "purchase": {"partner": "Supplier", "go": "Mark in transit", "go_done": "On the road", "rcv": "We received",
                 "price": "Price paid (₦)", "confirm": "Confirm receipt", "ordered": "Ordered"},
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
        root.addLayout(self.steps)

        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_value = kpi("Order value", dark=True)
        self.k_paid = kpi("Paid")
        self.k_balance = kpi("Balance")
        self.k_lines = kpi("Lines")
        for k in (self.k_value, self.k_paid, self.k_balance, self.k_lines):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        info = QHBoxLayout()
        info.setSpacing(8)
        self.lbl_expected = QLabel("Expected delivery")
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
        self.btn_revert = QPushButton("Revert to draft")
        self.btn_revert.setObjectName("dcGhost")
        self.btn_cancel = QPushButton("Cancel order")
        self.btn_cancel.setObjectName("dcGhost")
        for b in (self.btn_add, self.btn_remove, self.btn_fill, self.btn_go, self.btn_confirm):
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
        self.slot_hist = QWidget()
        self.slot_hist.setLayout(QVBoxLayout())
        self.slot_hist.layout().setContentsMargins(0, 0, 0, 0)
        for s in (self.slot_lines, self.slot_pay, self.slot_hist):
            self.stack.addWidget(s)

        tabs = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setObjectName("msSheets")
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.ElideNone)
        for text in ("Order lines", "Payments", "History"):
            self.tabs.addTab(text)
        tabs.addWidget(self.tabs)
        tabs.addStretch(1)
        root.addLayout(tabs)

        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.btn_add.clicked.connect(lambda: self.grid.add_row())
        self.btn_remove.clicked.connect(lambda: self.grid.remove())
        self.btn_go.clicked.connect(self._go)
        self.btn_fill.clicked.connect(self._fill)
        self.btn_confirm.clicked.connect(self._confirm)
        self.btn_revert.clicked.connect(self._revert)
        self.btn_cancel.clicked.connect(self._cancel)
        self.chk_expected.toggled.connect(self._header_changed)
        self.expected.dateChanged.connect(lambda *_: self._header_changed())
        self.notes.editingFinished.connect(self._header_changed)
        self.grid: SheetGrid = None
        self.pay_grid: SheetGrid = None

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
                     or f"{self.w['partner']} details not set")
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
        self.k_value.cap.setText("Order value" + (" (estimate)" if (not d or d["estimated"]) else " (final)"))
        self.k_paid.val.setText(money(d["paid"] if d else 0))
        if d and d["balance"] is not None:
            self.k_balance.val.setText(money(d["balance"]))
            self.k_balance.cap.setText("Still to pay" if self.kind == "purchase" else "Still to receive")
        else:
            self.k_balance.val.setText("-")
            self.k_balance.cap.setText("Balance after confirmation")
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
        self.btn_go.setVisible(adm and st == "draft")
        self.btn_fill.setVisible(st == "in_transit")
        self.btn_confirm.setVisible(st == "in_transit")
        self.btn_revert.setVisible(adm and st == "in_transit")
        self.btn_cancel.setVisible(adm and st == "draft")

    # ================================================================== lines grid
    def _line_cells(self, l: Dict[str, Any]) -> Dict[str, Any]:
        label = next((k for k, v in self.labels.items() if v == l["product_id"]), l["name"])
        return {"product": label, "bags": l["bags"] or "", "kg": l["kg"] or "", "price": l["price"] or "",
                "unit": l["qty_unit"] if "qty_unit" in l else ("bags" if l["type"] == "fertilizer" else "kg"),
                "ordered": l["bags"] if l["type"] == "fertilizer" else l["kg"], "oprice": l["price"],
                "rcv": "" if l.get("rcv_qty") is None else l["rcv_qty"], "rej": "" if l.get("rej_qty") is None else l["rej_qty"],
                "rprice": "" if l.get("rcv_price") is None else l["rcv_price"]}

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
                {"key": "kg", "label": "Weight (kg)\ncrops only", "type": "number", "width": 130,
                 "readonly": not self.is_admin},
                {"key": "price", "label": "Price (₦)\nper kg / per bag", "type": "money", "width": 150,
                 "readonly": not self.is_admin},
            ]
            computed = [{"key": "total", "label": "Line total\nauto", "width": 160}]
            kw = dict(first_edit_key="product", placeholder_key="product", defaults={},
                      ready=self._ready_line, on_add=self._add_line, on_edit=self._edit_line,
                      on_remove=self._remove_line, allow_add=self.is_admin,
                      hint="Type in the last row: pick the product, then bags (and kg for crops) and the price - the "
                           "row saves itself. Click a cell to change it. Right-click a row to remove it. A draft "
                           "changes no stock until it is " + w["go_done"].lower() + ".")
        else:
            lock = st != "in_transit"
            cols = [
                {"key": "product", "label": "Product", "type": "text", "width": 260, "stretch": True, "readonly": True},
                {"key": "unit", "label": "Unit", "type": "text", "width": 60, "readonly": True},
                {"key": "ordered", "label": f"{w['ordered']}\nqty", "type": "number", "width": 110, "readonly": True},
                {"key": "oprice", "label": "Order price (₦)", "type": "money", "width": 130, "readonly": True},
                {"key": "rcv", "label": f"{w['rcv']}\nqty", "type": "number", "width": 160, "readonly": lock},
                {"key": "rej", "label": "Rejected\nqty", "type": "number", "width": 100, "readonly": lock},
                {"key": "rprice", "label": w["price"], "type": "money", "width": 140, "readonly": lock},
            ]
            computed = [{"key": "acc", "label": "Accepted\nauto", "width": 110},
                        {"key": "amount", "label": "Line amount\nauto", "width": 150}]
            kw = dict(first_edit_key="rcv", ready=None, on_add=None, on_edit=self._edit_result, allow_add=False,
                      hint=("Enter, for each line, how much " + ("the company received" if self.kind == "sales" else "we received")
                            + ", how much was rejected and the price; “Accepted” and the amount work themselves out. "
                            "Entries are kept as you type - use Confirm when every line is done."
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
            comp = {"total": money(self._line_total(l)), "acc": acc, "amount": amount}
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
    def _product(self, label: str) -> Dict[str, Any]:
        pid = self.labels.get((label or "").strip())
        if pid is None:
            raise ValueError("Pick the product from the list.")
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
        hint = ("Type a payment in the last row (amount and how). " +
                ("Before the order is confirmed it is kept as an advance and counts once the final price is set. "
                 if d and d["status"] != "confirmed" else "It cannot be more than the balance. ") +
                "Remove a row to void it - a reason is required.") if can_pay else (
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

    # ================================================================== history
    def _build_history(self) -> None:
        d = self.d
        events = []
        for e in (d["timeline"] if d else []):
            events.append((e["at"], e["event"], e["by"], ""))
        for p in (d["payments"] if d else []):
            note = f"Payment {money(p['amount'])} ({(p['method'] or '').title()})"
            events.append((p["created_at"], note + (" - VOIDED" if p["status"] == "voided" else ""), p["created_by"],
                           p["notes"]))
        events.sort(key=lambda e: (e[0] is None, e[0]))
        cols = [{"key": "at", "label": "When", "type": "text", "width": 150, "readonly": True},
                {"key": "event", "label": "What happened", "type": "text", "width": 360, "stretch": True,
                 "readonly": True},
                {"key": "by", "label": "By", "type": "text", "width": 140, "readonly": True},
                {"key": "note", "label": "Note", "type": "text", "width": 240, "readonly": True}]
        rows = [{"id": i, "locked": True,
                 "cells": {"at": e[0].strftime("%d/%m/%Y %H:%M") if e[0] else "", "event": e[1], "by": e[2],
                           "note": e[3]}} for i, e in enumerate(events)]
        grid = SheetGrid(columns=cols, allow_add=False, hint="Everything that has happened to this order, in order.")
        grid.set_rows(rows, {"event": f"{len(rows)} event(s)"})
        self._swap(self.slot_hist, grid)
