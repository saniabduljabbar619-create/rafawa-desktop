# apps/product_management_app/features/profiling/views/walkins_list.py
# -*- coding: utf-8 -*-
"""
Walk-ins - its own dedicated screen now, not a tab buried inside Customers.

Walk-ins are isolated from registered Customers and Suppliers by design (see
app_state.get_or_lazy_create_walkin and the walk-in-role guard in
SaleService/PurchaseService._ensure_role) - they deliberately never appear in
those lists.

This screen no longer dumps the entire walk-in roster on open. With 50+
shops all funnelling casual traders into the same shared pool, that list
gets long fast and mostly isn't what the operator wants to see - they
usually have exactly one name in mind. So the screen shows a single
bolded "Walk-in space" row instead; clicking it opens WalkinSearchDialog
to search by name/phone, and whichever trader gets picked there is the
only row that ever lands on this screen, replacing the placeholder.

Double-click still opens the full activity history, exactly as before
(same CustomerProfileView component every registered customer/supplier
profile uses - a walk-in is just a Party row like any other, so nothing
about that view needed to change).

Right-click adds a shortcut menu for the common day-to-day actions
(Make Payment, Record Advance, Returns) so a cashier doesn't have to open
the full profile just to record one payment - each dialog already accepts
a generic `party` object, and a walk-in satisfies that shape identically
to a registered customer. The context menu only makes sense once a real
trader is loaded, so it's a no-op while the placeholder row is showing.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QMenu, QDialog
)
from PySide2.QtGui import QFont
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
from apps.product_management_app.features.profiling.dialogs.walkin_search_dialog import WalkinSearchDialog, find_walkins, weighings_text
from shared.uix.layout.scrollable import make_scrollable_page

PLACEHOLDER_TEXT = "\U0001F50D  Walk-in space \u2014 type a name, phone or weighing ID in the box above"


class WalkinsListView(QWidget):
    def __init__(self, mode="admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()

        root = make_scrollable_page(self, margins=(12, 12, 12, 12))

        title = QLabel("Walk-ins")
        title.setObjectName("Title")
        root.addWidget(title)

        subtitle = QLabel(
            "Casual walk-in customers and suppliers, isolated from the "
            "regular Customers and Suppliers lists. Click the row below "
            "to search for one by name or phone."
        )
        subtitle.setStyleSheet("color: #666;")
        root.addWidget(subtitle)

        # Toolbar - just a way back to search once a trader is loaded;
        # the old free-text filter bar is gone, replaced by WalkinSearchDialog.
        top = QHBoxLayout()
        root.addLayout(top)
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Search a walk-in by name, phone or weighing ID (e.g. 12 or WT-00012)...")
        self.search_in.setClearButtonEnabled(True)
        top.addWidget(self.search_in, 1)
        self.btn_search = QPushButton("New Search")
        top.addWidget(self.btn_search)

        # Table - always exactly one row: either the bolded placeholder,
        # or the one trader currently loaded from search.
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Full Name", "Phone / Weighings", "Status"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        root.addWidget(self.table, 1)

        # Actions
        actions = QHBoxLayout()
        root.addLayout(actions)
        self.btn_delete = QPushButton("Delete")
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)

        # Cashiers may not delete walk-in records - same rule the old tab had.
        if self.mode == "cashier":
            self.btn_delete.setVisible(False)

        # Wiring
        self.btn_search.clicked.connect(self._new_search)
        self.search_in.textChanged.connect(self._on_search_text)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        self._open_profiles = []
        self._current_walkin = None
        self._results = []
        self.refresh()

    def selected_walkin_id(self):
        if self._current_walkin is None:
            return None
        return int(self._current_walkin.id)

    def _selected_walkin(self):
        return self._current_walkin

    def _new_search(self):
        self._current_walkin = None
        self.search_in.clear()
        self.search_in.setFocus()
        self.refresh()

    def _on_search_text(self, _text):
        self._current_walkin = None
        self.refresh()

    def _on_selection(self):
        if not self._results:
            return
        row = self.table.currentRow()
        if 0 <= row < len(self._results):
            self._current_walkin = self._results[row]

    def _render_results(self):
        self.table.setRowCount(0)
        for w in self._results:
            r = self.table.rowCount()
            self.table.insertRow(r)
            pend = getattr(w, "pending", 0)
            status = f"{pend} waiting for cashier" if pend else w.status
            for i, val in enumerate([str(w.id), w.full_name, w.phone or weighings_text(w), status]):
                it = QTableWidgetItem(val)
                if i == 0:
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, i, it)

    def refresh(self):
        """Typed search: list the matches. Otherwise the single placeholder / loaded row."""
        self._results = find_walkins(self.search_in.text())
        if self.search_in.text().strip():
            if self._results:
                self._render_results()
                # keep the same walk-in selected across a refresh
                return
            self.table.setRowCount(0)
            self.table.insertRow(0)
            it = QTableWidgetItem("No walk-in matches that.")
            it.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(0, 0, it)
            self.table.setSpan(0, 0, 1, 4)
            return
        if self._current_walkin is not None and not getattr(self._current_walkin, "walkin_name", None):
            # trader may have been deleted elsewhere since it was loaded
            self._current_walkin = next(
                (x for x in state.walkins if x.id == self._current_walkin.id), None
            )
        if self._current_walkin is None:
            self._render_placeholder()
        else:
            self._render_loaded(self._current_walkin)

    def _render_placeholder(self):
        self.table.setRowCount(0)
        self.table.insertRow(0)
        it = QTableWidgetItem(PLACEHOLDER_TEXT)
        it.setTextAlignment(Qt.AlignCenter)
        f = QFont()
        f.setBold(True)
        it.setFont(f)
        self.table.setItem(0, 0, it)
        self.table.setSpan(0, 0, 1, 4)

    def _render_loaded(self, w):
        self.table.setRowCount(0)
        self.table.insertRow(0)
        cols = [str(w.id), w.full_name, w.phone, w.status]
        for i, val in enumerate(cols):
            it = QTableWidgetItem(val)
            if i == 0:
                it.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(0, i, it)

    def _on_cell_clicked(self, row, _col):
        if self._results and 0 <= row < len(self._results):
            self._current_walkin = self._results[row]
        elif self._current_walkin is None:
            self.search_in.setFocus()

    def _on_cell_double_clicked(self, row, _col):
        if self._results and 0 <= row < len(self._results):
            self._current_walkin = self._results[row]
        if self._current_walkin is None:
            self.search_in.setFocus()
        else:
            self.open_profile()

    def open_search(self):
        dlg = WalkinSearchDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted and dlg.selected_walkin is not None:
            self._current_walkin = dlg.selected_walkin
            self.refresh()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        if self._results and row < len(self._results):
            self._current_walkin = self._results[row]
        w = self._selected_walkin()
        if not w:
            return

        named = getattr(w, "walkin_name", None)
        menu = QMenu(self)
        act_payment = menu.addAction("Make Payment")
        act_advance = menu.addAction("Record Advance")
        act_return = menu.addAction("Go to Returns")
        if named:        # these share one account across every walk-in, so only the name-scoped payment is safe
            act_advance.setVisible(False)
            act_return.setVisible(False)
        menu.addSeparator()
        act_profile = menu.addAction("Open Full Activity")
        menu.addSeparator()
        act_delete = menu.addAction("Delete")
        if self.mode == "cashier":
            act_delete.setEnabled(False)

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen == act_payment:
            self._open_payment(w)
        elif chosen == act_advance:
            self._open_advance(w)
        elif chosen == act_return:
            self._open_returns(w)
        elif chosen == act_profile:
            self.open_profile()
        elif chosen == act_delete:
            self.delete_selected()

    def _open_payment(self, w):
        named = getattr(w, "walkin_name", None)
        if named:
            from apps.product_management_app.features.profiling.dialogs.payment_target_dialog import PaymentTargetDialog
            PaymentTargetDialog(party=w, user_id=state.require_user_id(), parent=self, walkin_name=named).exec_()
            self.refresh()
            return
        from apps.product_management_app.features.profiling.dialogs.payment_target_dialog import PaymentTargetDialog
        dlg = PaymentTargetDialog(party=w, user_id=state.require_user_id(), parent=self)
        dlg.exec_()
        self.refresh()

    def _open_advance(self, w):
        from apps.product_management_app.features.profiling.dialogs.advance_pay_dialog import AdvancePayDialog
        dlg = AdvancePayDialog(party=w, user_id=state.require_user_id(), parent=self)
        dlg.exec_()
        self.refresh()

    def _open_returns(self, w):
        # AdminReturnsView is a full screen, not a dialog - wrapped here the
        # same way SuppliersListPage wraps SupplierProfileView. Walk-ins are
        # deliberately excluded from its general Party dropdown (they live
        # only here, in their own screen) - preselect_party_id is the one
        # exception, injecting THIS specific walk-in and selecting it
        # automatically, so right-clicking here and choosing this action
        # lands the operator ready to create the return immediately,
        # without having to search for the same name again.
        from apps.product_management_app.features.profiling.views.admin_returns import AdminReturnsView
        dlg = QDialog(self)
        dlg.setWindowTitle("Returns")
        # AdminReturnsView already has its own sticky-footer fix (Create
        # Return(s) stays pinned no matter how many products get added) -
        # but this WRAPPER dialog had no min/max buttons at all, so if it
        # ever opened too small for the screen it was shown on, there was
        # no way to resize it back to something usable. Same fix as
        # drawer_screen.py and sale_checkout_dialog.py earlier this session.
        dlg.setWindowFlags(
            (dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
        )
        dlg.resize(1200, 850)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(AdminReturnsView(mode=self.mode, preselect_party_id=int(w.id)))
        dlg.exec_()

    def delete_selected(self):
        w = self._current_walkin
        if not w:
            QMessageBox.information(self, "Delete Walk-in", "Search for a trader first.")
            return

        if getattr(w, "walkin_name", None):
            QMessageBox.information(self, "Delete Walk-in",
                                    "This name comes from weighings and sales, so there is no record to delete.")
            return

        ok = QMessageBox.question(
            self, "Confirm Delete", f"Delete walk-in record for '{w.full_name}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok == QMessageBox.Yes:
            wid = int(w.id)
            if hasattr(state, 'delete_walkin'):
                state.delete_walkin(wid)
            else:
                state.walkins = [x for x in state.walkins if x.id != wid]
            self._current_walkin = None  # deleted - back to placeholder
            self.refresh()

    def open_profile(self):
        w = self._selected_walkin()
        if not w:
            return

        named = getattr(w, "walkin_name", None)
        profile = CustomerProfileView(None, mode=self.mode, walkin_name=named) if named \
            else CustomerProfileView(w, mode=self.mode)
        profile.setWindowTitle(f"Walk-in Trader Profile - {w.full_name}")
        profile.resize(900, 560)

        self._open_profiles.append(profile)
        profile.destroyed.connect(lambda: self._cleanup_profile(profile))
        profile.show()

    def _cleanup_profile(self, w):
        try:
            self._open_profiles = [x for x in self._open_profiles if x is not w]
        except Exception:
            pass