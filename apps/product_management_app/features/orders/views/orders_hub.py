# apps/product_management_app/features/orders/views/orders_hub.py
"""
Orders - the one place for selling goods to companies (Sales Orders) and buying from suppliers (Purchase Orders).

    Home          two big buttons to start an order, the orders that need attention with plain words for what to do
                  next, and three small figures. Double-click an order to open it.
    New order     one question - which company / supplier - then the order sheet opens.
    Companies and suppliers   the people orders are made for (their profile holds all their orders), one click away.

Companies and suppliers are one shared pool (OrderHubService.sync_partners), so a partner added on one side is
pickable on the other.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.order_hub_service import OrderHubService
from shared.uix.layout.flow import FlowLayout, ResponsiveRow
from shared.uix.widgets.cards import KpiCard, Segmented, money, style_table

PARTNER_TABS = [("sales", "Companies we sell to"), ("purchase", "Suppliers we buy from")]
FILTERS = [("open", "Needs attention"), ("finished", "Finished"), ("all", "All orders")]
COLS = ["Order", "Company / supplier", "Type", "Status", "What to do next", "Value", "Still unpaid"]
_STATUS_COLOR = {"draft": "#6b7280", "in_transit": "#1d4ed8", "confirmed": "#047857", "cancelled": "#b91c1c"}


def _day(dt) -> str:
    return dt.strftime("%d %b %Y") if dt else "-"


def needs_attention(r) -> bool:
    """Not finished: still a draft, still on the road, or delivered but not fully paid."""
    if r["status"] in ("draft", "in_transit"):
        return True
    return r["status"] == "confirmed" and (r["balance"] or 0) > 0.005


def status_words(r) -> str:
    if r["status"] == "draft":
        return "Draft"
    if r["status"] == "in_transit":
        d = r["days_out"]
        base = "On the road"
        return base + ("" if d is None else (" - today" if d == 0 else f" - {d} day{'s' if d != 1 else ''}"))
    if r["status"] == "confirmed":
        return "Delivered" if (r["balance"] or 0) > 0.005 else "Delivered and paid"
    return "Cancelled"


def next_step_words(r) -> str:
    """The one thing to do with this order, in plain words."""
    sales = r["kind"] == "sales"
    if r["status"] == "draft":
        return "Add the products, then press Dispatch" if sales else "Add the products, then mark it on the road"
    if r["status"] == "in_transit":
        late = "LATE - " if r["overdue"] else ""
        return late + ("Confirm delivery when the goods arrive" if sales else "Confirm receipt when the goods arrive")
    if r["status"] == "confirmed":
        if (r["balance"] or 0) > 0.005:
            return (f"Collect the payment ({money(r['balance'])})" if sales
                    else f"Pay the supplier ({money(r['balance'])})")
        return "Nothing - finished"
    return "Nothing - cancelled"


class OrdersBoard(QWidget):
    """The list of orders."""

    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self._rows = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        bar = FlowLayout()
        self.status_seg = Segmented(FILTERS)
        bar.addWidget(self.status_seg)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search an order number or a company / supplier name")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(34)
        self.search.setMinimumWidth(320)
        bar.addWidget(self.search)
        self.btn_refresh = QPushButton("Refresh")
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        style_table(self.table, 44)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        foot = FlowLayout()
        self.btn_open = QPushButton("Open the order")
        self.btn_open.setObjectName("PrimaryButton")
        self.btn_track = QPushButton("Order history")
        self.btn_track.setToolTip("Who did what to the selected order, and when")
        foot.addWidget(self.btn_open)
        foot.addWidget(self.btn_track)
        hint = QLabel("Double-click an order to open it.")
        hint.setStyleSheet("color:#6b7280;")
        foot.addWidget(hint)
        root.addLayout(foot)

        self.status_seg.changed.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_track.clicked.connect(self._track)
        self.table.cellDoubleClicked.connect(lambda *_: self._open_selected())

    def reload(self, *_a) -> None:
        key = self.status_seg.key()
        rows = OrderHubService.board("", "", self.search.text())
        if key == "open":
            rows = [r for r in rows if needs_attention(r)]
        elif key == "finished":
            rows = [r for r in rows if not needs_attention(r)]
        self._rows = rows
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["order_no"], r["partner"], "Sale" if r["kind"] == "sales" else "Purchase", status_words(r),
                    next_step_words(r),
                    money(r["value"]) + (" (est.)" if r["estimated"] and r["value"] else ""),
                    money(r["balance"]) if r["balance"] and r["balance"] > 0.005 else "-"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, (r["kind"], r["id"]))
                it.setToolTip(f"Created {_day(r['created_at'])}" + (f"   Expected {_day(r['expected'])}" if r["expected"] else ""))
                if c == 3:
                    it.setForeground(QColor("#b45309" if r["overdue"] else _STATUS_COLOR.get(r["status"], "#111827")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if c == 4 and r["overdue"]:
                    it.setForeground(QColor("#b91c1c"))
                if c in (5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Orders", "Click an order first.")
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
            lines.append("\nThe expected delivery date has passed - still on the road.")
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

        # ---- the header: only on the home page (deeper pages need the room)
        self.header = QWidget()
        hl = QVBoxLayout(self.header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("Orders")
        t.setObjectName("Title")
        s = QLabel("Sell goods to a company, or buy goods from a supplier. Open an order to see exactly what to do next.")
        s.setWordWrap(True)
        s.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(t)
        titles.addWidget(s)
        hl.addLayout(titles)

        actions = FlowLayout(h_spacing=10)
        self.btn_new_sales = QPushButton("+  New Sales Order      (sell to a company)")
        self.btn_new_sales.setObjectName("PrimaryButton")
        self.btn_new_purchase = QPushButton("+  New Purchase Order      (buy from a supplier)")
        self.btn_new_purchase.setObjectName("PrimaryButton")
        self.btn_partners = QPushButton("Companies and suppliers")
        self.btn_partners.setToolTip("The people orders are made for - open one to see all their orders")
        for b in (self.btn_new_sales, self.btn_new_purchase):
            b.setMinimumHeight(42)
        self.btn_partners.setMinimumHeight(42)
        for b in (self.btn_new_sales, self.btn_new_purchase, self.btn_partners):
            actions.addWidget(b)
        hl.addLayout(actions)

        kp = ResponsiveRow(spacing=12)
        self.kpi_open = KpiCard("Orders needing attention", "0", "", "blue")
        self.kpi_owed = KpiCard("Owed to us", "0.00", "money companies still have to pay", "green")
        self.kpi_owe = KpiCard("We owe", "0.00", "money we still have to pay suppliers", "amber")
        for k in (self.kpi_open, self.kpi_owed, self.kpi_owe):
            kp.addWidget(k)
        hl.addLayout(kp)
        root.addWidget(self.header)

        # ---- partners pages carry a way back
        self.tabs_row = QWidget()
        row = QHBoxLayout(self.tabs_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.btn_home = QPushButton("<  Back to orders")
        self.tabs = Segmented(PARTNER_TABS, default_first=False)
        row.addWidget(self.btn_home)
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
        self.btn_home.clicked.connect(lambda: self.show_tab("board"))
        self.btn_new_sales.clicked.connect(lambda: self.new_order("sales"))
        self.btn_new_purchase.clicked.connect(lambda: self.new_order("purchase"))
        self.btn_partners.clicked.connect(lambda: self.show_tab("sales"))
        self.btn_new_sales.setVisible(self.is_admin)
        self.btn_new_purchase.setVisible(self.is_admin)
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
        home = key == "board"
        if key in ("sales", "purchase"):
            self.tabs.set_key(key)
        else:
            self.tabs.clear()
        page = self._page(key)
        if hasattr(page, "refresh"):
            page.refresh()
        if home:
            self.board.reload()
        self.stack.setCurrentWidget(page)
        self.header.setVisible(home)
        self.tabs_row.setVisible(not home)
        self.refresh_kpis()

    # ---------------------------------------------------------------- starting an order
    def new_order(self, kind: str) -> None:
        """Ask which company / supplier, then open a blank order sheet for them."""
        from apps.product_management_app.features.orders.dialogs.partner_pick_dialog import PartnerPickDialog
        dlg = PartnerPickDialog(kind, self, can_add=self.is_admin)
        if dlg.exec_() == dlg.Accepted and dlg.partner_id:
            self.open_order(kind, int(dlg.partner_id), None)

    # ---------------------------------------------------------------- deeper pages (profile, order sheet)
    def _push(self, page) -> None:
        self._trail.append(self.stack.currentWidget())
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.tabs_row.setVisible(False)
        self.header.setVisible(False)

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
        if not self._trail:
            home = prev is self.board
            self.header.setVisible(home)
            self.tabs_row.setVisible(not home)
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
        self.show_tab(self.tabs.key() or "board")

    def refresh_kpis(self) -> None:
        rows = OrderHubService.board("", "", "", limit=5000)
        todo = [r for r in rows if needs_attention(r)]
        late = sum(1 for r in todo if r["overdue"])
        sales = sum(1 for r in todo if r["kind"] == "sales")
        self.kpi_open.set(str(len(todo)), f"{sales} sales, {len(todo) - sales} purchases" + (f"   -   {late} LATE" if late else ""))
        owed = sum((r["balance"] or 0) for r in rows if r["kind"] == "sales" and r["balance"] is not None)
        owe = sum((r["balance"] or 0) for r in rows if r["kind"] == "purchase" and r["balance"] is not None)
        self.kpi_owed.set(money(owed))
        self.kpi_owe.set(money(owe))
