# apps/product_management_app/features/orders/views/orders_hub.py
"""
Orders - ONE menu entry for Sales Orders and Purchase Orders.

    Tracking board   every order of both kinds in one list: status, how long it has been on the road,
                     overdue deliveries, value, payment state. Double-click to open, Track for history.
    Sales Orders     companies we sell to (their profile holds the orders)
    Purchase Orders  suppliers we buy from

Companies and suppliers are one shared pool (OrderHubService.sync_partners), so a partner added on one
side is pickable on the other.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.order_hub_service import OrderHubService
from shared.uix.widgets.cards import KpiCard, Segmented, money, style_table

TABS = [("board", "All orders"), ("sales", "Sales Orders - companies"), ("purchase", "Purchase Orders - suppliers")]
KIND_FILTER = [("", "All"), ("sales", "Sales"), ("purchase", "Purchase")]
STATUS_FILTER = [("active", "Not finished"), ("draft", "Draft"), ("in_transit", "On the road"),
                 ("confirmed", "Confirmed"), ("cancelled", "Cancelled"), ("", "All")]
COLS = ["Type", "Order", "Company / supplier", "Status", "Created", "Days on the road", "Expected", "Value", "Payment"]
_STATUS_TEXT = {"draft": "Draft", "in_transit": "On the road", "confirmed": "Confirmed", "cancelled": "Cancelled"}
_STATUS_COLOR = {"draft": "#6b7280", "in_transit": "#1d4ed8", "confirmed": "#047857", "cancelled": "#b91c1c"}


def _day(dt) -> str:
    return dt.strftime("%d %b %Y") if dt else "-"


class OrdersBoard(QWidget):
    """The unified tracking list."""

    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self._rows = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        bar = QHBoxLayout()
        self.kind_seg = Segmented(KIND_FILTER)
        self.status_seg = Segmented(STATUS_FILTER)
        bar.addWidget(self.kind_seg)
        bar.addWidget(self.status_seg)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search an order number or a company / supplier name")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(34)
        bar.addWidget(self.search, 1)
        self.btn_refresh = QPushButton("Refresh")
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        style_table(self.table, 42)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.btn_open = QPushButton("Open")
        self.btn_open.setObjectName("PrimaryButton")
        self.btn_track = QPushButton("Order history")
        self.btn_new_sales = QPushButton("+ New Sales Order")
        self.btn_new_purchase = QPushButton("+ New Purchase Order")
        for b in (self.btn_open, self.btn_track):
            foot.addWidget(b)
        foot.addStretch(1)
        foot.addWidget(self.btn_new_sales)
        foot.addWidget(self.btn_new_purchase)
        root.addLayout(foot)

        self.kind_seg.changed.connect(self.reload)
        self.status_seg.changed.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_track.clicked.connect(self._track)
        self.table.cellDoubleClicked.connect(lambda *_: self._open_selected())
        self.btn_new_sales.clicked.connect(lambda: hub.show_tab("sales"))
        self.btn_new_purchase.clicked.connect(lambda: hub.show_tab("purchase"))

    def reload(self, *_a) -> None:
        self._rows = OrderHubService.board(self.kind_seg.key(), self.status_seg.key(), self.search.text())
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            road = "-"
            if r["days_out"] is not None:
                road = "today" if r["days_out"] == 0 else f"{r['days_out']} day(s)"
            pay = (r["payment_status"] or "-").replace("_", " ").title()
            if r["balance"]:
                pay += f"  ({money(r['balance'])} due)"
            vals = ["Sales" if r["kind"] == "sales" else "Purchase", r["order_no"], r["partner"],
                    _STATUS_TEXT.get(r["status"], r["status"]) + ("  - OVERDUE" if r["overdue"] else ""),
                    _day(r["created_at"]), road, _day(r["expected"]),
                    money(r["value"]) + (" (est.)" if r["estimated"] and r["value"] else ""), pay]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, (r["kind"], r["id"]))
                if c == 3:
                    it.setForeground(QColor("#b45309" if r["overdue"] else _STATUS_COLOR.get(r["status"], "#111827")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if c == 7:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Orders", "Select an order first.")
            return None
        return self._rows[row]

    def _open_selected(self) -> None:
        r = self._selected()
        if r is not None:
            self.hub.open_order(r["kind"], r["partner_id"], r["id"])

    def _track(self) -> None:
        r = self._selected()
        if r is None:
            return
        try:
            events = OrderHubService.timeline(r["kind"], r["id"])
        except ValueError as e:
            QMessageBox.warning(self, "Orders", str(e))
            return
        lines = [f"{e['at'].strftime('%d %b %Y %H:%M') if e['at'] else ''}   {e['event']}"
                 + (f"   (by {e['by']})" if e["by"] else "") for e in events]
        if r["overdue"]:
            lines.append("\nExpected delivery date has passed - still on the road.")
        QMessageBox.information(self, f"{r['order_no']} - {r['partner']}", "\n".join(lines) or "No history yet.")


class OrdersHubView(QWidget):
    def __init__(self, mode: str = "admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()
        self.is_admin = self.mode == "admin"
        try:
            OrderHubService.sync_partners()
        except Exception:                       # an un-migrated database must not block the screen
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("Orders")
        t.setObjectName("Title")
        s = QLabel("Every Sales Order (we sell to a company) and Purchase Order (we buy from a supplier), from draft to "
                   "delivery. To start a new order, open Sales Orders or Purchase Orders and pick the company / supplier.")
        s.setWordWrap(True)
        s.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(t)
        titles.addWidget(s)
        root.addLayout(titles)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_sales = KpiCard("Sales on the road", "0", "", "blue")
        self.kpi_purchase = KpiCard("Purchases on the road", "0", "", "amber")
        self.kpi_overdue = KpiCard("Overdue deliveries", "0", "past expected date", "red")
        self.kpi_owed = KpiCard("Order balances", "0.00", "", "green")
        for k in (self.kpi_sales, self.kpi_purchase, self.kpi_overdue, self.kpi_owed):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        self.tabs = Segmented(TABS)
        self.tabs_row = QWidget()
        row = QHBoxLayout(self.tabs_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.tabs)
        row.addStretch(1)
        root.addWidget(self.tabs_row)
        self._trail = []

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.board = OrdersBoard(self)
        self.stack.addWidget(self.board)
        self._pages = {"board": self.board}
        self.tabs.changed.connect(self.show_tab)
        self.show_tab("board")

    def _page(self, key: str):
        if key not in self._pages:              # built on first visit
            from apps.product_management_app.features.orders.views.partners_page import PartnersPage
            page = PartnersPage(key, is_admin=self.is_admin)
            page.open_partner.connect(lambda pid, k=key: self.open_profile(k, pid))
            self._pages[key] = page
            self.stack.addWidget(page)
        return self._pages[key]

    def show_tab(self, key: str) -> None:
        self._close_deep_pages()
        self.tabs.set_key(key)
        page = self._page(key)
        if hasattr(page, "refresh"):
            page.refresh()
        if key == "board":
            self.board.reload()
        self.stack.setCurrentWidget(page)
        self.refresh_kpis()

    # ---------------------------------------------------------------- deeper pages (profile, order sheet)
    def _push(self, page) -> None:
        self._trail.append(self.stack.currentWidget())
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.tabs_row.setVisible(False)

    def _pop(self) -> None:
        if not self._trail:
            return
        page = self.stack.currentWidget()
        prev = self._trail.pop()
        self.stack.setCurrentWidget(prev)
        self.stack.removeWidget(page)
        page.deleteLater()
        for fn in ("reload", "refresh"):
            if hasattr(prev, fn):
                getattr(prev, fn)()
                break
        self.tabs_row.setVisible(not self._trail)
        self.refresh_kpis()

    def _close_deep_pages(self) -> None:
        while self._trail:
            self._pop()

    def open_profile(self, kind: str, partner_id: int) -> None:
        from apps.product_management_app.features.orders.views.partner_profile_page import PartnerProfilePage
        page = PartnerProfilePage(kind, partner_id, is_admin=self.is_admin)
        page.back.connect(self._pop)
        page.open_order.connect(lambda oid, k=kind, p=partner_id: self.open_order(k, p, oid))
        page.new_order.connect(lambda k=kind, p=partner_id: self.open_order(k, p, None))
        self._push(page)

    def open_order(self, kind: str, partner_id: int, order_id=None) -> None:
        from apps.product_management_app.features.orders.views.order_sheet_page import OrderSheetPage
        page = OrderSheetPage(kind, partner_id, order_id, is_admin=self.is_admin)
        page.back.connect(self._pop)
        page.changed.connect(self.refresh_kpis)
        self._push(page)

    def refresh(self) -> None:
        self.show_tab(self.tabs.key())

    def refresh_kpis(self) -> None:
        ov = OrderHubService.overview()
        s, p = ov["sales"], ov["purchase"]
        self.kpi_sales.set(str(s["in_transit"]), f"{money(s['in_transit_value'])} in transit  |  {s['draft']} draft")
        self.kpi_purchase.set(str(p["in_transit"]), f"{money(p['in_transit_value'])} in transit  |  {p['draft']} draft")
        self.kpi_overdue.set(str(s["overdue"] + p["overdue"]), f"sales {s['overdue']}  |  purchase {p['overdue']}")
        self.kpi_owed.set(money(s["outstanding"] + p["outstanding"]),
                          f"owed to us {money(s['outstanding'])}  |  we owe {money(p['outstanding'])}")
