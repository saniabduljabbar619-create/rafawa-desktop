# apps/product_management_app/features/sales_orders/views/sales_orders_view.py
# -*- coding: utf-8 -*-
"""
Sales Orders - selling crops to companies. Entirely separate from the
existing Sale/checkout flow, per the client's own explicit design:
different screens, different workflow, connected to the rest of the app
only through the real stock and party effects they eventually cause.

Role rules (from the client's own requirements):
  Admin   - create, edit, cancel, and dispatch Sales Orders.
  Cashier - cannot create, edit, or cancel. Can finalize (confirm) an
            existing order only when it's already in_transit, and only
            when Admin is unavailable - that "unavailable" condition is a
            business-process rule the audit log records (who actually
            confirmed it, and when), not something this screen enforces
            technically. Both roles can reach Confirm; only Admin can
            reach Create/Dispatch/Cancel.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from database.session import get_session
from database.models.sales_order import SalesOrder
from database.models.party import Party
from shared.uix.layout.scrollable import make_scrollable_page

_STATUS_LABELS = {
    "draft": "Draft",
    "in_transit": "In Transit",
    "confirmed": "Confirmed",
    "cancelled": "Cancelled",
}


class SalesOrdersView(QWidget):
    def __init__(self, mode: str = "admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()
        self.is_admin = self.mode == "admin"

        root = make_scrollable_page(self, margins=(12, 12, 12, 12))

        title = QLabel("Sales Orders")
        title.setObjectName("Title")
        root.addWidget(title)

        subtitle = QLabel(
            "Selling crops to companies - goods leave the warehouse and "
            "sit in transit until the company confirms weight, price, and "
            "what they're actually accepting."
        )
        subtitle.setStyleSheet("color: #666;")
        root.addWidget(subtitle)

        # Toolbar
        top = QHBoxLayout()
        root.addLayout(top)

        top.addWidget(QLabel("Filter:"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("All", None)
        for key, label in _STATUS_LABELS.items():
            self.status_filter.addItem(label, key)
        top.addWidget(self.status_filter)

        top.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Order no. / Buyer name…")
        top.addWidget(self.search_in, 1)

        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)

        # Table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Order No.", "Buyer (Company)", "Status", "Dispatched", "Confirmed", "Final Amount"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(320)
        root.addWidget(self.table, 1)

        # Actions
        actions = QHBoxLayout()
        root.addLayout(actions)

        self.btn_create = QPushButton("Create Order")
        self.btn_edit_draft = QPushButton("Edit Draft")
        self.btn_dispatch = QPushButton("Dispatch")
        self.btn_confirm = QPushButton("Confirm (Finalize)")
        self.btn_revert = QPushButton("Revert to Draft")
        self.btn_cancel = QPushButton("Cancel Order")
        actions.addWidget(self.btn_create)
        actions.addWidget(self.btn_edit_draft)
        actions.addWidget(self.btn_dispatch)
        actions.addWidget(self.btn_confirm)
        actions.addWidget(self.btn_revert)
        actions.addWidget(self.btn_cancel)
        actions.addStretch(1)

        if not self.is_admin:
            self.btn_create.setVisible(False)
            self.btn_edit_draft.setVisible(False)
            self.btn_dispatch.setVisible(False)
            self.btn_revert.setVisible(False)
            self.btn_cancel.setVisible(False)

        # Wiring
        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.table.cellClicked.connect(self._sync_action_state)
        self.table.cellDoubleClicked.connect(self._open_detail)

        self.btn_create.clicked.connect(self._open_create)
        self.btn_edit_draft.clicked.connect(self._edit_draft_selected)
        self.btn_dispatch.clicked.connect(self._dispatch_selected)
        self.btn_confirm.clicked.connect(self._confirm_selected)
        self.btn_revert.clicked.connect(self._revert_selected)
        self.btn_cancel.clicked.connect(self._cancel_selected)

        self._orders_cache = {}
        self.refresh()
        self._sync_action_state()

    # ------------------------------------------------------------------
    def refresh(self):
        q = self.search_in.text().strip().lower()
        status_key = self.status_filter.currentData()

        with get_session() as s:
            orders = s.query(SalesOrder).order_by(SalesOrder.created_at.desc()).all()
            party_ids = {int(o.buyer_party_id) for o in orders}
            parties = {
                int(p.id): p.display_name
                for p in s.query(Party).filter(Party.id.in_(party_ids)).all()
            } if party_ids else {}

        rows = []
        for o in orders:
            buyer_name = parties.get(int(o.buyer_party_id), f"Party#{o.buyer_party_id}")
            if status_key and o.status != status_key:
                continue
            if q and q not in o.order_no.lower() and q not in buyer_name.lower():
                continue
            rows.append((o, buyer_name))

        self._orders_cache = {o.id: o for o, _ in rows}

        self.table.setRowCount(0)
        for o, buyer_name in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            dispatched_s = o.dispatched_at.strftime("%Y-%m-%d %H:%M") if o.dispatched_at else ""
            confirmed_s = o.confirmed_at.strftime("%Y-%m-%d %H:%M") if o.confirmed_at else ""
            final_s = f"{float(o.final_total_amount):,.2f}" if o.final_total_amount is not None else ""
            cells = [
                o.order_no, buyer_name, _STATUS_LABELS.get(o.status, o.status),
                dispatched_s, confirmed_s, final_s,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, o.id)
                if c >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)

        self._sync_action_state()

    def _selected_order(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 0)
        if not item:
            return None
        order_id = item.data(Qt.UserRole)
        return self._orders_cache.get(order_id)

    def _sync_action_state(self, *_args):
        order = self._selected_order()
        status = order.status if order else None

        self.btn_edit_draft.setEnabled(self.is_admin and status == "draft")
        self.btn_dispatch.setEnabled(self.is_admin and status == "draft")
        self.btn_cancel.setEnabled(self.is_admin and status == "draft")
        self.btn_confirm.setEnabled(status == "in_transit")
        self.btn_revert.setEnabled(self.is_admin and status == "in_transit")

    # ------------------------------------------------------------------
    def _open_detail(self, row, _col):
        item = self.table.item(row, 0)
        if not item:
            return
        order_id = item.data(Qt.UserRole)
        if not order_id:
            return

        from apps.product_management_app.features.sales_orders.dialogs.sales_order_detail_dialog import (
            SalesOrderDetailDialog,
        )
        dlg = SalesOrderDetailDialog(sales_order_id=order_id, parent=self)
        dlg.exec_()

    def _open_create(self):
        from apps.product_management_app.features.sales_orders.dialogs.sales_order_create_dialog import (
            SalesOrderCreateDialog,
        )
        dlg = SalesOrderCreateDialog(parent=self)
        if dlg.exec_() == dlg.Accepted:
            self.refresh()

    def _edit_draft_selected(self):
        order = self._selected_order()
        if not order:
            QMessageBox.information(self, "Edit Draft", "Select a draft order first.")
            return

        from apps.product_management_app.features.sales_orders.dialogs.sales_order_create_dialog import (
            SalesOrderCreateDialog,
        )
        dlg = SalesOrderCreateDialog(parent=self, sales_order_id=order.id)
        if dlg.exec_() == dlg.Accepted:
            self.refresh()

    def _revert_selected(self):
        order = self._selected_order()
        if not order:
            QMessageBox.information(self, "Revert to Draft", "Select an in-transit order first.")
            return

        confirm = QMessageBox.question(
            self, "Confirm Revert",
            f"Revert {order.order_no} back to draft? This will fully "
            f"reverse the stock deduction from dispatch, and clear any "
            f"confirmation feedback already saved for it - genuinely "
            f"starting the dispatch process over. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from database.services.sales_order_service import SalesOrderService
        try:
            SalesOrderService.revert_to_draft(order.id, reverted_by_user_id=state.require_user_id())
        except Exception as e:
            QMessageBox.critical(self, "Revert Failed", str(e))
            return

        QMessageBox.information(self, "Reverted", f"{order.order_no} is back to draft.")
        self.refresh()

    def _dispatch_selected(self):
        order = self._selected_order()
        if not order:
            QMessageBox.information(self, "Dispatch", "Select a draft order first.")
            return

        confirm = QMessageBox.question(
            self, "Confirm Dispatch",
            f"Dispatch {order.order_no}? This will deduct the ordered "
            f"quantities from Available Stock and move this order to "
            f"In Transit. This cannot be undone with a simple cancel.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from database.services.sales_order_service import SalesOrderService
        try:
            SalesOrderService.dispatch(order.id, dispatched_by_user_id=state.require_user_id())
        except Exception as e:
            QMessageBox.critical(self, "Dispatch Failed", str(e))
            return

        QMessageBox.information(self, "Dispatched", f"{order.order_no} is now In Transit.")
        self.refresh()

    def _confirm_selected(self):
        order = self._selected_order()
        if not order:
            QMessageBox.information(self, "Confirm", "Select an in-transit order first.")
            return

        from apps.product_management_app.features.sales_orders.dialogs.sales_order_confirm_dialog import (
            SalesOrderConfirmDialog,
        )
        dlg = SalesOrderConfirmDialog(sales_order_id=order.id, parent=self)
        if dlg.exec_() == dlg.Accepted:
            self.refresh()

    def _cancel_selected(self):
        order = self._selected_order()
        if not order:
            QMessageBox.information(self, "Cancel Order", "Select a draft order first.")
            return

        confirm = QMessageBox.question(
            self, "Confirm Cancel",
            f"Cancel {order.order_no}? Since it hasn't been dispatched yet, "
            f"nothing has touched stock - this simply withdraws it.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from database.services.sales_order_service import SalesOrderService
        try:
            SalesOrderService.cancel(order.id, cancelled_by_user_id=state.require_user_id())
        except Exception as e:
            QMessageBox.critical(self, "Cancel Failed", str(e))
            return

        QMessageBox.information(self, "Cancelled", f"{order.order_no} has been cancelled.")
        self.refresh()