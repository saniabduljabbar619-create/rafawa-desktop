# apps/product_management_app/features/cashier/dialogs/quick_purchase_dialog.py
# -*- coding: utf-8 -*-
"""
The Quick Purchase interlude.

Fires ONLY when a checkout is short on stock. Shows the operator, in plain
language, exactly what is missing and offers the three real-world choices:

    1. Quick Purchase  - buy the shortfall from a supplier right now
    2. Backorder       - take payment, owe the customer the missing goods
    3. Sell available  - sell only what is in stock, no obligation

For Quick Purchase it collects one supplier, the dealer cost per short product,
and a pay-now / pay-later intention. It DOES NOT execute anything - it returns a
decision, and the caller (cashier_sales._checkout) drives the services. A dialog
decides; it does not do.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QComboBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QFrame, QMessageBox, QWidget, QCheckBox
)

from database.services.supplier_profile_service import SupplierProfileService
from PySide2.QtWidgets import QInputDialog

NAIRA = "\u20a6"
RED = "#c0392b"
GREEN = "#1a7f37"
GREY = "#6b7280"

# decisions the dialog can return
DECISION_QUICK = "quick_purchase"
DECISION_BACKORDER = "backorder"
DECISION_AVAILABLE = "sell_available"


class QuickPurchaseDialog(QDialog):

    def __init__(self, availability: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.availability = availability or {}
        self.short = list(self.availability.get("short", []))
        self._cost_spins: Dict[int, QDoubleSpinBox] = {}

        self.setWindowTitle("Some products are short")
        self.setModal(True)
        self.setMinimumWidth(640)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- the plain-language shortfall summary ----
        head = QLabel("Not everything the customer ordered is in stock.")
        head.setStyleSheet("font-size:16px; font-weight:800;")
        root.addWidget(head)

        summary = QLabel(self.availability.get("human", ""))
        summary.setWordWrap(True)
        summary.setStyleSheet(f"color:{RED}; font-size:13px;")
        root.addWidget(summary)

        # ---- the three choices ----
        self.grp = QButtonGroup(self)
        self.rb_quick = QRadioButton("Buy the missing items now (Quick Purchase)")
        self.rb_back = QRadioButton("Take payment now, owe the customer the rest (Backorder)")
        self.rb_avail = QRadioButton("Sell only what is in stock")
        for i, rb in enumerate((self.rb_quick, self.rb_back, self.rb_avail)):
            rb.setStyleSheet("font-size:14px; font-weight:600;")
            self.grp.addButton(rb, i)
            root.addWidget(rb)
        self.rb_quick.setChecked(True)
        self.grp.buttonClicked.connect(self._sync_quick_panel)

        # ---- the quick-purchase panel (only relevant for choice 1) ----
        self.panel = self._build_quick_panel()
        root.addWidget(self.panel)

        # ---- buttons ----
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Continue")
        ok.setStyleSheet(
            f"QPushButton {{ background:{GREEN}; color:#fff; border:none;"
            f" border-radius:8px; padding:8px 20px; font-weight:800; }}")
        ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

        self._sync_quick_panel()

    # ------------------------------------------------------------------
    def _build_quick_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("QFrame { border:1px solid #e5e7eb; border-radius:10px; }")
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        # supplier picker, recently-used first
        row = QHBoxLayout()
        row.addWidget(QLabel("Buy from supplier:"))
        self.supplier_cb = QComboBox()
        self._load_suppliers()
        row.addWidget(self.supplier_cb, 1)
        v.addLayout(row)

        # which missing products does THIS quick purchase cover? Product-level
        # choice, not partial quantity - default is everything checked, since
        # usually the goal is to cover the whole shortfall in one go.
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Buy which missing items:"))
        sel_row.addStretch(1)
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        btn_all.clicked.connect(lambda: self._set_all_checks(True))
        btn_none.clicked.connect(lambda: self._set_all_checks(False))
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        v.addLayout(sel_row)

        # per-short-product select + dealer cost
        self._select_checks: Dict[int, QCheckBox] = {}
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Buy?", "Missing product", "Shortfall", "Dealer cost each"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setRowCount(len(self.short))
        for i, ln in enumerate(self.short):
            pid = int(ln["product_id"])
            name = ln.get("name") or ln.get("product_name") or f"Product#{pid}"
            ptype = str(ln.get("product_type") or "").lower()
            if ptype == "fertilizer":
                short_txt = f"{ln.get('short_qty_bags', 0)} bag(s)"
            else:
                short_txt = f"{ln.get('short_weight_kg', 0):g} kg"

            chk = QCheckBox()
            chk.setChecked(True)  # default: cover everything unless told otherwise
            self._select_checks[pid] = chk
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setAlignment(Qt.AlignCenter)
            cl.addWidget(chk)
            self.table.setCellWidget(i, 0, cell)

            self.table.setItem(i, 1, QTableWidgetItem(str(name)))
            self.table.setItem(i, 2, QTableWidgetItem(short_txt))
            spin = QDoubleSpinBox()
            spin.setRange(0, 99_999_999)
            spin.setDecimals(2)
            spin.setGroupSeparatorShown(True)
            spin.setPrefix(NAIRA)
            self._cost_spins[pid] = spin
            self.table.setCellWidget(i, 3, spin)
        v.addWidget(self.table)

        # pay now / later intention (recorded at receive, not now)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Payment to supplier:"))
        self.pay_cb = QComboBox()
        self.pay_cb.addItem("Pay now (cash out today)", "paid")
        self.pay_cb.addItem("Pay later (supplier payable)", "credit")
        prow.addWidget(self.pay_cb, 1)
        v.addLayout(prow)

        note = QLabel("Only the missing quantity is bought. What is already in "
                      "stock is never re-purchased.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{GREY}; font-size:11px;")
        v.addWidget(note)
        return panel

    NEW_SUPPLIER = "__new__"

    def _set_all_checks(self, checked: bool):
        for chk in self._select_checks.values():
            chk.setChecked(checked)

    def _load_suppliers(self):
        self.supplier_cb.blockSignals(True)
        self.supplier_cb.clear()
        try:
            suppliers = SupplierProfileService.active_suppliers()
        except Exception as e:
            # Never swallow this silently again - an empty list here looks
            # identical to "no suppliers registered" and hid a real bug once.
            suppliers = []
            QMessageBox.warning(self, "Suppliers",
                                f"Could not load the supplier list:\n{e}\n\n"
                                "You can still add a new supplier below.")
        # "+ New supplier" is always first - the dealer being short is exactly
        # when a NEW dealer often shows up, so the counter can never dead-end.
        self.supplier_cb.addItem("+ New supplier...", self.NEW_SUPPLIER)
        for sup in suppliers:
            label = sup["name"]
            if sup.get("phone"):
                label += f"  ({sup['phone']})"
            self.supplier_cb.addItem(label, int(sup["id"]))
        self.supplier_cb.setEnabled(True)
        if suppliers:
            self.supplier_cb.setCurrentIndex(1)  # most-recently-used by default
        self.supplier_cb.blockSignals(False)
        if not hasattr(self, "_supplier_signal_wired"):
            self.supplier_cb.currentIndexChanged.connect(self._on_supplier_pick)
            self._supplier_signal_wired = True

    def _on_supplier_pick(self, _idx: int):
        if self.supplier_cb.currentData() != self.NEW_SUPPLIER:
            return
        self._prompt_new_supplier()

    def _prompt_new_supplier(self) -> bool:
        """
        Runs the create-supplier prompt. Returns True and leaves the combo on
        the new supplier if one was created; False (combo left as-is) if the
        operator cancelled. Called both when "+ New supplier..." is picked
        directly AND when Continue is pressed while it is still selected -
        a fresh deployment with zero suppliers opens straight onto this entry,
        so Continue must not silently accept the sentinel value.
        """
        name, ok = QInputDialog.getText(self, "New Supplier", "Supplier name:")
        name = (name or "").strip()
        if not ok or not name:
            self.supplier_cb.setCurrentIndex(0 if self.supplier_cb.count() <= 1 else 1)
            return False
        phone, _ = QInputDialog.getText(self, "New Supplier",
                                        "Phone (optional, press OK to skip):")
        try:
            sid = SupplierProfileService.get_or_lazy_create_supplier(
                name, (phone or "").strip())
        except Exception as e:
            QMessageBox.critical(self, "New Supplier", str(e))
            self.supplier_cb.setCurrentIndex(0)
            return False
        self._load_suppliers()
        for i in range(self.supplier_cb.count()):
            if self.supplier_cb.itemData(i) == sid:
                self.supplier_cb.setCurrentIndex(i)
                return True
        return False  # should not happen, but never leave the sentinel selected

    def _sync_quick_panel(self):
        self.panel.setVisible(self.rb_quick.isChecked())

    # ------------------------------------------------------------------
    def _accept(self):
        try:
            self._do_accept()
        except Exception as e:
            # Whatever the real cause turns out to be, it must never look
            # like "nothing happened" when Continue is clicked.
            QMessageBox.critical(self, "Quick Purchase",
                                 f"Could not continue:\n{e}")

    def _do_accept(self):
        if self.rb_back.isChecked() or self.rb_avail.isChecked():
            self.accept()
            return

        # quick purchase - validate
        sid = self.supplier_cb.currentData()
        if sid == self.NEW_SUPPLIER:
            # Nothing chosen yet, but "+ New supplier..." IS what they want -
            # a zero-supplier deployment opens straight onto this entry, so
            # Continue should run the same prompt rather than just complain.
            if not self._prompt_new_supplier():
                return  # operator cancelled the create prompt
            sid = self.supplier_cb.currentData()
        if not sid or sid == self.NEW_SUPPLIER:
            QMessageBox.warning(self, "Quick Purchase", "Pick a supplier to buy from.")
            return

        # Which missing products is THIS purchase covering? At least one.
        selected = [ln for ln in self.short
                   if self._select_checks[int(ln["product_id"])].isChecked()]
        if not selected:
            QMessageBox.warning(
                self, "Quick Purchase",
                "Check at least one product to buy, or choose Backorder / "
                "Sell available instead.")
            return

        # Dealer cost defaults to 0 and is easy to miss - name the exact
        # product still missing a price and highlight its field. Only checked
        # (selected) rows are required; an unchecked row's cost is irrelevant.
        missing_names = []
        for ln in selected:
            pid = int(ln["product_id"])
            spin = self._cost_spins[pid]
            empty = spin.value() <= 0
            spin.setStyleSheet(
                "QDoubleSpinBox { border: 2px solid #c0392b; background: #fff5f5; }"
                if empty else "")
            if empty:
                missing_names.append(ln.get("name") or ln.get("product_name")
                                     or f"Product#{pid}")
        if missing_names:
            QMessageBox.warning(
                self, "Quick Purchase",
                "Enter the dealer cost for: " + ", ".join(missing_names))
            return

        self.accept()

    # ------------------------------------------------------------------
    def decision(self) -> Dict[str, Any]:
        """What the operator chose. The caller acts on this."""
        if self.rb_back.isChecked():
            return {"decision": DECISION_BACKORDER}
        if self.rb_avail.isChecked():
            return {"decision": DECISION_AVAILABLE}

        selected = [ln for ln in self.short
                   if self._select_checks[int(ln["product_id"])].isChecked()]
        unselected = [ln for ln in self.short
                     if not self._select_checks[int(ln["product_id"])].isChecked()]
        return {
            "decision": DECISION_QUICK,
            "supplier_party_id": int(self.supplier_cb.currentData()),
            # only SELECTED products are being bought this round
            "selected_short": selected,
            # anything left unchecked - the caller drops these from the sale,
            # same treatment as choosing "Sell available" would give them
            "unselected_short": unselected,
            "dealer_cost_by_pid": {
                int(pid): float(spin.value())
                for pid, spin in self._cost_spins.items()
            },
            "payment_intention": self.pay_cb.currentData(),
        }