# apps/product_management_app/features/cashier/dialogs/sale_checkout_dialog.py
# -*- coding: utf-8 -*-
"""
Final checkout confirmation.

Rate is now LIVE and editable per line, not frozen. This matters for Quick
Purchase: a product that never had a sale price (default_sale_rate = 0) can
only get its real price set here, AFTER the shortfall is known and resolved -
there is no earlier point in the flow where the operator can meaningfully
price something they didn't yet know they could sell.

CRITICAL: this dialog does not itself change what gets sold. The caller
(cashier_sales.py) must read payload()["rate_overrides"] (keyed by
product_id) and apply it to the items actually sent to SaleService.record().
Editing the number here and doing nothing with it would be worse than the
old read-only field - it would look like it worked while silently recording
the stale price.
"""
from __future__ import annotations

from typing import Dict, List

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
    QDoubleSpinBox, QComboBox, QRadioButton, QButtonGroup, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from shared.uix.layout.scrollable import make_scrollable_dialog


class SaleCheckoutDialog(QDialog):
    def __init__(self, parent=None, customer_id=None, customer_label="", walkin_name="",
                 total=0.0, total_cost=0.0, total_profit=0.0, lines=None):
        super().__init__(parent)
        self.setWindowTitle("Checkout")
        self.setModal(True)
        self.setMinimumWidth(720)
        # Genuinely resizable and maximizable - with more than a handful of
        # products this dialog's content easily outgrows any fixed size,
        # exactly the "Confirm button pushed off-screen" problem this fixes.
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
        )
        self.resize(900, 720)

        # Each line: {"product_id", "name", "rate", "bags", "kg",
        #             "product_type", "subtotal"}
        self._lines = list(lines or [])

        # The Confirm/Cancel row lives in `footer`, OUTSIDE the scroll area -
        # it never scrolls away, no matter how many product rows get added
        # to `root` above it. This is the direct fix for the exact complaint:
        # 4+ products pushing Confirm off the bottom of the screen.
        root, footer = make_scrollable_dialog(self, margins=(12, 12, 12, 12), spacing=10)

        # Header summary
        root.addWidget(QLabel(f"Customer: {customer_label}"))
        if walkin_name:
            root.addWidget(QLabel(f"Walk-in name: {walkin_name}"))

        # Declared before the group-discount panel below (which wires signals
        # that reference these) even though they're only actually FILLED once
        # the main grid loop runs further down - defensive: an early signal
        # fire would iterate zero rows harmlessly instead of raising.
        self._rate_spins = []
        self._disc_spins = []
        self._sub_items = []
        self._total_items = []
        self._basis_qty = []

        # -------- Quick discount by product --------
        # The SAME product can legitimately appear as several separate cart
        # rows (added at different times, or split across cost lots by
        # Quick Purchase) - typing the same per-bag/per-kg discount into
        # each row by hand is exactly the repetitive step this removes.
        # One control per DISTINCT product; changing it re-distributes
        # across every row that shares that product_id.
        self._product_row_indices: Dict[int, List[int]] = {}
        for idx, ln in enumerate(self._lines):
            pid = ln.get("product_id")
            if pid is None:
                continue
            self._product_row_indices.setdefault(int(pid), []).append(idx)

        self._group_spins: Dict[int, QDoubleSpinBox] = {}
        if len(self._product_row_indices) > 0:
            root.addWidget(QLabel(
                "Quick discount by product (per bag/kg - applies to every "
                "row of that product below):"))
            group_box = QFrame()
            group_grid = QGridLayout(group_box)
            group_grid.setContentsMargins(8, 8, 8, 8)
            group_grid.setHorizontalSpacing(14)
            group_grid.setVerticalSpacing(6)

            for i, pid in enumerate(self._product_row_indices.keys()):
                first_row = self._product_row_indices[pid][0]
                name = str(self._lines[first_row].get("name") or f"Product#{pid}")
                row_count = len(self._product_row_indices[pid])
                label_text = name if row_count == 1 else f"{name} ({row_count} rows)"

                group_grid.addWidget(QLabel(label_text), i, 0)
                gspin = QDoubleSpinBox()
                gspin.setDecimals(2)
                gspin.setRange(0.0, 1_000_000_000.0)
                gspin.setValue(0.0)
                gspin.setPrefix("₦")
                gspin.valueChanged.connect(
                    lambda val, _pid=pid: self._apply_group_discount(_pid, val))
                group_grid.addWidget(gspin, i, 1)
                self._group_spins[pid] = gspin

            root.addWidget(group_box)

        # -------- Itemized rate + discount grid --------
        root.addWidget(QLabel(
            "Confirm the selling price for each product, then apply a "
            "discount if needed (leave 0 for none):"))

        self.grid = QTableWidget(len(self._lines), 6)
        self.grid.setHorizontalHeaderLabels(
            ["Product", "Rate", "Bags", "Subtotal", "Discount", "Total"]
        )
        self.grid.verticalHeader().setVisible(False)
        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.setSelectionMode(QTableWidget.NoSelection)
        hh = self.grid.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.grid.setMinimumHeight(160)
        root.addWidget(self.grid, 1)

        for r, ln in enumerate(self._lines):
            ptype = str(ln.get("product_type") or "").strip().lower()
            is_fert = ptype in ("fertilizer", "fertilizers")
            basis_qty = float(ln.get("bags") or 0) if is_fert else float(ln.get("kg") or 0)
            self._basis_qty.append(basis_qty)

            def _ro(txt, right=True):
                cell = QTableWidgetItem(txt)
                if right:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                return cell

            self.grid.setItem(r, 0, QTableWidgetItem(str(ln.get("name") or "")))

            # RATE - live and editable, unlike before. A product priced at 0
            # (never sold, e.g. straight out of Quick Purchase) is exactly
            # what this field exists to fix.
            rate_spin = QDoubleSpinBox()
            rate_spin.setDecimals(2)
            rate_spin.setRange(0.0, 1_000_000_000.0)
            rate_spin.setValue(float(ln.get("rate") or 0.0))
            if float(ln.get("rate") or 0.0) <= 0.0:
                rate_spin.setStyleSheet(
                    "QDoubleSpinBox { border: 2px solid #c0392b; background: #fff5f5; }")
            rate_spin.valueChanged.connect(self._recompute)
            self.grid.setCellWidget(r, 1, rate_spin)
            self._rate_spins.append(rate_spin)

            self.grid.setItem(r, 2, _ro(str(int(ln.get("bags") or 0))))

            sub_item = _ro(f"{basis_qty * float(ln.get('rate') or 0.0):,.2f}")
            self.grid.setItem(r, 3, sub_item)
            self._sub_items.append(sub_item)

            spin = QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(0.0, 1_000_000_000.0)   # max tightened live in _recompute
            spin.setValue(0.0)
            spin.valueChanged.connect(self._recompute)
            self.grid.setCellWidget(r, 4, spin)
            self._disc_spins.append(spin)

            tot_item = _ro(f"{basis_qty * float(ln.get('rate') or 0.0):,.2f}")
            self.grid.setItem(r, 5, tot_item)
            self._total_items.append(tot_item)

        # Grand total (updates live)
        self.lbl_total = QLabel("")
        self.lbl_total.setStyleSheet("font-weight: 800; font-size: 14px;")
        root.addWidget(self.lbl_total)

        # -------- Payment mode --------
        box = QFrame()
        box.setObjectName("Card")
        b = QVBoxLayout(box)
        b.setContentsMargins(10, 10, 10, 10)
        b.setSpacing(8)

        b.addWidget(QLabel("Payment status:"))

        self.rb_paid = QRadioButton("Paid now")
        self.rb_not_paid = QRadioButton("Not paid (record as advance/outstanding)")
        self.rb_paid.setChecked(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_paid)
        self.mode_group.addButton(self.rb_not_paid)

        b.addWidget(self.rb_paid)
        b.addWidget(self.rb_not_paid)

        arow = QHBoxLayout()
        arow.addWidget(QLabel("Amount paid (₦):"))
        self.amount_paid = QDoubleSpinBox()
        self.amount_paid.setDecimals(2)
        self.amount_paid.setMinimum(0.0)
        self.amount_paid.setMaximum(1_000_000_000.0)
        arow.addWidget(self.amount_paid, 1)
        b.addLayout(arow)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Method:"))
        self.method = QComboBox()
        self.method.addItems(["cash", "transfer", "pos", "other"])
        mrow.addWidget(self.method, 1)
        b.addLayout(mrow)

        root.addWidget(box)

        # Note
        nrow = QHBoxLayout()
        nrow.addWidget(QLabel("Note:"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional…")
        nrow.addWidget(self.note, 1)
        root.addLayout(nrow)

        # Footer - lives in the PINNED footer layout (see make_scrollable_dialog
        # above), not root, so it's always reachable regardless of content height.
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Confirm")
        self.btn_ok.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_ok)

        # wiring
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_confirm)
        self.rb_paid.toggled.connect(self._sync_enabled)

        self._sync_enabled()
        self._auto_amount_value = 0.0
        self._recompute()

        self._payload = None

    # ---------------- helpers ----------------

    def _total_discount(self) -> float:
        return round(sum(float(s.value()) for s in self._disc_spins), 2)

    def _apply_group_discount(self, product_id: int, per_unit: float):
        """
        Distribute a per-bag/per-kg discount across every row sharing this
        product_id - the whole point of the group panel. Values still land
        in each row's OWN discount spin, so any individual row can still be
        corrected by hand afterward without disturbing the group control.
        """
        row_indices = self._product_row_indices.get(int(product_id), [])
        for idx in row_indices:
            if idx >= len(self._disc_spins):
                continue
            basis = self._basis_qty[idx]
            disc_spin = self._disc_spins[idx]
            disc_spin.blockSignals(True)
            disc_spin.setValue(round(basis * float(per_unit), 2))
            disc_spin.blockSignals(False)
        self._recompute()

    def _grand_subtotal(self) -> float:
        """Live sum of every line's basis_qty * CURRENT rate - not the
        value frozen when the dialog opened."""
        return round(sum(
            basis * float(rate_spin.value())
            for basis, rate_spin in zip(self._basis_qty, self._rate_spins)
        ), 2)

    def _recompute(self):
        # Per-line subtotal/total, driven by the LIVE rate, not a frozen one.
        for basis, rate_spin, disc_spin, sub_item, tot_item in zip(
                self._basis_qty, self._rate_spins, self._disc_spins,
                self._sub_items, self._total_items):
            sub = round(basis * float(rate_spin.value()), 2)
            sub_item.setText(f"{sub:,.2f}")

            # a rate edit can shrink the subtotal below whatever discount was
            # already typed - keep the discount from exceeding its own line
            if disc_spin.value() > sub:
                disc_spin.blockSignals(True)
                disc_spin.setValue(sub)
                disc_spin.blockSignals(False)

            line_total = max(sub - float(disc_spin.value()), 0.0)
            tot_item.setText(f"{line_total:,.2f}")

        grand_subtotal = self._grand_subtotal()
        grand = max(grand_subtotal - self._total_discount(), 0.0)
        self.lbl_total.setText(
            f"Total after discount: ₦{grand:,.2f}   "
            f"(discount ₦{self._total_discount():,.2f})"
        )
        self._sync_amount_default(grand)

    def _sync_enabled(self):
        paid = self.rb_paid.isChecked()
        self.amount_paid.setEnabled(paid)
        self.method.setEnabled(paid)

    def _sync_amount_default(self, grand: float):
        # Keep "amount paid" tracking the LIVE grand total, unless the
        # operator has deliberately typed a partial that differs from
        # whatever we last auto-set (a real manual override, not just the
        # value we happened to put there ourselves).
        if not self.rb_paid.isChecked():
            return
        cur = float(self.amount_paid.value())
        if abs(cur - self._auto_amount_value) < 0.01 or cur > grand + 0.01:
            self.amount_paid.setValue(grand)
        self._auto_amount_value = grand

    def _on_confirm(self):
        discount = self._total_discount()
        grand_subtotal = self._grand_subtotal()
        grand = max(grand_subtotal - discount, 0.0)

        # Any rate left at 0.00 is a real problem for Quick Purchase items -
        # selling for free is almost certainly a mistake, not intent.
        zero_rate_names = [
            str(ln.get("name") or "")
            for ln, rate_spin in zip(self._lines, self._rate_spins)
            if float(rate_spin.value()) <= 0.0
        ]
        if zero_rate_names:
            from PySide2.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Checkout",
                "Set a selling price for: " + ", ".join(zero_rate_names))
            return

        # product_id -> final rate, for the caller to apply to whatever it
        # actually sends to SaleService.record() (including split QP lines).
        rate_overrides = {
            int(ln["product_id"]): float(rate_spin.value())
            for ln, rate_spin in zip(self._lines, self._rate_spins)
            if "product_id" in ln
        }

        # product_id -> this line's own discount amount, exactly as typed
        # in its own row of the grid - previously only the SUM of every
        # row (self._total_discount()) ever left this dialog, silently
        # collapsing which product actually got how much discount.
        discount_overrides = {
            int(ln["product_id"]): float(disc_spin.value())
            for ln, disc_spin in zip(self._lines, self._disc_spins)
            if "product_id" in ln
        }

        if self.rb_paid.isChecked():
            amt = float(self.amount_paid.value())
            if amt <= 0.0:
                self.amount_paid.setFocus()
                return
            if amt > grand + 0.01:
                self.amount_paid.setValue(grand)
                amt = grand

            self._payload = {
                "discount": discount,
                "grand_subtotal": grand_subtotal,
                "rate_overrides": rate_overrides,
                "discount_overrides": discount_overrides,
                "payment_mode": "paid",
                "amount_paid": amt,
                "method": str(self.method.currentText() or "cash"),
                "note": str(self.note.text() or "").strip(),
            }
        else:
            self._payload = {
                "discount": discount,
                "grand_subtotal": grand_subtotal,
                "rate_overrides": rate_overrides,
                "discount_overrides": discount_overrides,
                "payment_mode": "not_paid",
                "amount_paid": 0.0,
                "method": "",
                "note": str(self.note.text() or "").strip(),
            }

        self.accept()

    def payload(self) -> dict:
        return dict(self._payload or {})