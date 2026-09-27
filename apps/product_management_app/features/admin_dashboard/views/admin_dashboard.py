# apps\product_management_app\features\admin_dashboard\views\admin_dashboard.py

from __future__ import annotations
from datetime import date as _date
from PySide2.QtCore import Qt, QDate
from PySide2.QtGui import QFont
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableWidget,
    QTableWidgetItem, QPushButton, QTabWidget, QCheckBox, QDateEdit
)

from apps.product_management_app.features.admin_dashboard.views.purchase_settlement_dialog import PurchaseSettlementDialog
from .sale_settlement_dialog import SaleSettlementDialog
from database.services.dashboard_service import (
    get_admin_dashboard_snapshot,
    get_purchase_payment_snapshot,
    get_sale_payment_snapshot,
)
from shared.uix.layout.scrollable import make_scrollable_page


# =============================
# Card Helper (Clickable Ready)
# =============================
def _card(
    title: str,
    big_value: str,
    subtitle: str = "",
    accent: str = "neutral",
    on_click=None,
) -> QFrame:

    palette = {
        "neutral": ("#FFFFFF", "#E6E6E6"),
        "blue":    ("#EEF4FF", "#BFD4FF"),
        "teal":    ("#E8F7F3", "#B7E6D8"),
        "amber":   ("#FFF6E5", "#FFD79A"),
        "orange":  ("#FFF0E6", "#FFC1A3"),
        "green":   ("#E9F8EE", "#A7E7BF"),
        "red":     ("#FDEAEA", "#F3A7A7"),
    }

    bg, bd = palette.get(accent, palette["neutral"])

    w = QFrame()
    w.setObjectName("Card")
    w.setStyleSheet(
        f"""
        QFrame#Card {{
            background: {bg};
            border: 1px solid {bd};
            border-radius: 12px;
        }}
        """
    )

    lay = QVBoxLayout(w)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(6)

    t = QLabel(title)
    t.setStyleSheet("font-size: 12px; color: #111; opacity: 0.85;")
    lay.addWidget(t)

    v = QLabel(big_value)
    v.setStyleSheet("font-size: 26px; font-weight: 900; color: #0B0B0B;")
    lay.addWidget(v)

    if subtitle:
        s = QLabel(subtitle)
        s.setStyleSheet("font-size: 12px; color: #111; opacity: 0.75;")
        s.setWordWrap(True)
        lay.addWidget(s)

    lay.addStretch(1)

    if on_click:
        def handler(event):
            on_click()
        w.mousePressEvent = handler
        w.setCursor(Qt.PointingHandCursor)

    return w


def _fmt_money(x: float) -> str:
    return f"₦{float(x or 0):,.2f}"


def _fmt_num(x: float) -> str:
    return f"{float(x or 0):,.2f}"


# =============================
# Dashboard View
# =============================
class AdminDashboardView(QWidget):

    def __init__(self, current_user_id: int | None = None):
        super().__init__()

        # The logged-in admin's user id. Needed for the delete-with-audit
        # flow in the settlement dialogs (who deleted a row is logged
        # against this id). Wire in your real auth/session user id here
        # when constructing AdminDashboardView.
        self.current_user_id = current_user_id

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        root.addLayout(hdr)

        title = QLabel("Admin Dashboard")
        hdr.addWidget(title)
        hdr.addStretch(1)

        # Scopes ONLY "Fully Paid Sales"/"Fully Paid Purchases" - defaults to
        # TODAY so those two specific cards reset daily, per the client's
        # explicit request. Partial/Unpaid stay all-time regardless (an
        # unpaid balance from yesterday is still unpaid today - it doesn't
        # reset just because it's a new day), and nothing else on this
        # screen is affected.
        self.paid_date_lbl = QLabel("Fully Paid scope:")
        hdr.addWidget(self.paid_date_lbl)

        self.paid_date_chk = QCheckBox("Filter by date")
        self.paid_date_chk.stateChanged.connect(self._on_paid_dates)
        hdr.addWidget(self.paid_date_chk)

        self.paid_d_from = QDateEdit()
        self.paid_d_from.setCalendarPopup(True)
        self.paid_d_from.setDisplayFormat("yyyy-MM-dd")
        self.paid_d_from.setDate(QDate.currentDate())
        self.paid_d_from.setEnabled(False)
        self.paid_d_from.dateChanged.connect(self._on_paid_dates)
        hdr.addWidget(self.paid_d_from)

        self.paid_d_to = QDateEdit()
        self.paid_d_to.setCalendarPopup(True)
        self.paid_d_to.setDisplayFormat("yyyy-MM-dd")
        self.paid_d_to.setDate(QDate.currentDate())
        self.paid_d_to.setEnabled(False)
        self.paid_d_to.dateChanged.connect(self._on_paid_dates)
        hdr.addWidget(self.paid_d_to)

        # Unchecked default: today-today. Only reload() cares about the
        # checkbox; these two dates are always what gets used either way.
        self.paid_start_date = _date.today()
        self.paid_end_date = _date.today()

        self.btn_refresh = QPushButton("Refresh")
        hdr.addWidget(self.btn_refresh)
        self.btn_refresh.clicked.connect(self.refresh)

        # Tabs
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # ================= PAYMENTS TAB =================
        self.payments_tab = QWidget()
        self.tabs.addTab(self.payments_tab, "Payments")
        self.payments_layout = make_scrollable_page(self.payments_tab, margins=(4, 4, 4, 4))
        self.payments_layout.setSpacing(14)

        # ----- PURCHASE SECTION HEADER -----
        self.purchase_header = QLabel("Purchase Payment History")
        self.purchase_header.setStyleSheet("""
            font-size: 14px;
            font-weight: 800;
            color: #0B0B0B;
        """)
        self.payments_layout.addWidget(self.purchase_header)

        # Purchase cards row
        self.payments_row = QHBoxLayout()
        self.payments_row.setSpacing(10)
        self.payments_layout.addLayout(self.payments_row)

        # Divider
        self.payments_divider = QFrame()
        self.payments_divider.setFrameShape(QFrame.HLine)
        self.payments_divider.setStyleSheet("color: #E6E6E6;")
        self.payments_layout.addWidget(self.payments_divider)

        # ----- SALES SECTION HEADER -----
        self.sales_header = QLabel("Sales Payment History")
        self.sales_header.setStyleSheet("""
            font-size: 14px;
            font-weight: 800;
            color: #0B0B0B;
        """)
        self.payments_layout.addWidget(self.sales_header)

        # Sales cards row
        self.sales_row = QHBoxLayout()
        self.sales_row.setSpacing(10)
        self.payments_layout.addLayout(self.sales_row)

        self.payments_layout.addStretch(1)

        # ================= ADVANCES TAB =================
        self.advances_tab = QWidget()
        self.tabs.addTab(self.advances_tab, "Advances")

        self.advances_layout = make_scrollable_page(self.advances_tab, margins=(4, 4, 4, 4))
        self.advances_layout.setSpacing(12)

        # Top Row (Advance IN)
        self.advances_row_top = QHBoxLayout()
        self.advances_row_top.setSpacing(10)
        self.advances_layout.addLayout(self.advances_row_top)

        # Bottom Row (Advance OUT)
        self.advances_row_bottom = QHBoxLayout()
        self.advances_row_bottom.setSpacing(10)
        self.advances_layout.addLayout(self.advances_row_bottom)

        self.advances_layout.addStretch(1)

        # ================= PARTY BALANCES TAB =================
        # Aggregates every Unpaid + Partially Paid document per party.
        # Purchases and Sales are kept on two separate sub-tabs (never
        # merged or netted into one row/number), so admin can flip
        # between which suppliers we still need to pay and which
        # customers still need to pay us, without opening each
        # settlement dialog one by one, and without the two tables
        # stacking on top of each other and eating up vertical space.
        self.balances_tab = QWidget()
        self.tabs.addTab(self.balances_tab, "Party Balances")

        self.balances_layout = make_scrollable_page(self.balances_tab, margins=(4, 4, 4, 4))
        self.balances_layout.setSpacing(10)

        self.balances_subtabs = QTabWidget()
        self.balances_layout.addWidget(self.balances_subtabs, 1)

        # ---- Suppliers we owe (Purchases) ----
        self.balances_purchase_tab = QWidget()
        self.balances_subtabs.addTab(self.balances_purchase_tab, "Purchases")

        purchase_tab_layout = QVBoxLayout(self.balances_purchase_tab)
        purchase_tab_layout.setContentsMargins(8, 8, 8, 8)
        purchase_tab_layout.setSpacing(8)

        self.balances_purchase_header = QLabel("Unpaid + Partially Paid Purchases")
        self.balances_purchase_header.setStyleSheet("""
            font-size: 14px;
            font-weight: 800;
            color: #0B0B0B;
        """)
        purchase_tab_layout.addWidget(self.balances_purchase_header)

        self.balances_purchase_table = QTableWidget(0, 3)
        self.balances_purchase_table.setHorizontalHeaderLabels(
            ["Supplier", "Purchase Docs", "We Pay"]
        )
        self.balances_purchase_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.balances_purchase_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.balances_purchase_table.horizontalHeader().setStretchLastSection(True)
        self.balances_purchase_table.setSortingEnabled(False)
        purchase_tab_layout.addWidget(self.balances_purchase_table, 1)

        self.balances_purchase_footer = QLabel("")
        self.balances_purchase_footer.setStyleSheet("font-size: 13px; font-weight: 800; color: #111;")
        purchase_tab_layout.addWidget(self.balances_purchase_footer)

        # ---- Customers who owe us (Sales) ----
        self.balances_sale_tab = QWidget()
        self.balances_subtabs.addTab(self.balances_sale_tab, "Sales")

        sale_tab_layout = QVBoxLayout(self.balances_sale_tab)
        sale_tab_layout.setContentsMargins(8, 8, 8, 8)
        sale_tab_layout.setSpacing(8)

        self.balances_sale_header = QLabel("Unpaid + Partially Paid Sales")
        self.balances_sale_header.setStyleSheet("""
            font-size: 14px;
            font-weight: 800;
            color: #0B0B0B;
        """)
        sale_tab_layout.addWidget(self.balances_sale_header)

        self.balances_sale_table = QTableWidget(0, 3)
        self.balances_sale_table.setHorizontalHeaderLabels(
            ["Customer", "Sale Docs", "They Pay Us"]
        )
        self.balances_sale_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.balances_sale_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.balances_sale_table.horizontalHeader().setStretchLastSection(True)
        self.balances_sale_table.setSortingEnabled(False)
        sale_tab_layout.addWidget(self.balances_sale_table, 1)

        self.balances_sale_footer = QLabel("")
        self.balances_sale_footer.setStyleSheet("font-size: 13px; font-weight: 800; color: #111;")
        sale_tab_layout.addWidget(self.balances_sale_footer)

        # ================= STATISTICS TAB =================
        self.stats_tab = QWidget()
        self.tabs.addTab(self.stats_tab, "Statistics")
        self.stats_layout = make_scrollable_page(self.stats_tab, margins=(4, 4, 4, 4))
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(10)
        self.stats_layout.addLayout(self.stats_row)

        self.top_block = QFrame()
        self.top_block.setObjectName("Card")
        self.top_block.setStyleSheet("""
        QFrame#Card {
            background: #FFFFFF;
            border: 1px solid #E6E6E6;
            border-radius: 12px;
        }
        """)

        bl = QVBoxLayout(self.top_block)
        bl.setContentsMargins(12, 12, 12, 12)
        bl.setSpacing(8)

        h = QLabel("Top Crops (by stock KG)")
        h.setStyleSheet("font-size: 13px; font-weight: 800;")
        bl.addWidget(h)

        self.top_table = QTableWidget(0, 3)
        self.top_table.setHorizontalHeaderLabels(["Crop", "SKU", "Net KG"])
        self.top_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.top_table.horizontalHeader().setStretchLastSection(True)
        bl.addWidget(self.top_table)

        self.stats_layout.addWidget(self.top_block, 1)

        self.refresh()

    # =============================
    def _on_paid_dates(self):
        on = self.paid_date_chk.isChecked()
        self.paid_d_from.setEnabled(on)
        self.paid_d_to.setEnabled(on)
        if on:
            self.paid_start_date = self.paid_d_from.date().toPython()
            self.paid_end_date = self.paid_d_to.date().toPython()
        else:
            self.paid_start_date = _date.today()
            self.paid_end_date = _date.today()
        self.refresh()

    # =============================
    def _clear_layout(self, lay: QHBoxLayout):
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    # =============================
    def refresh(self):

        # Clear once only
        self._clear_layout(self.stats_row)
        self._clear_layout(self.advances_row_top)
        self._clear_layout(self.advances_row_bottom)
        self._clear_layout(self.payments_row)
        self._clear_layout(self.sales_row)

        # Changed scope="today" to scope="all" to keep absolute total numbers visible persistently
        snap = get_admin_dashboard_snapshot(scope="all")
        stock = snap.get("stock") or {}
        advances = snap.get("advances") or {}
        advance_in = advances.get("advance_in_remaining", 0)
        advance_out = advances.get("advance_out_remaining", 0)
        top = snap.get("top_crops") or []

        # ========== STATISTICS TAB ==========
        self.stats_row.addWidget(
            _card(
                "Fertilizer Stock",
                f"{_fmt_num(stock.get('fertilizer_bags'))} bags",
                "Net fertilizer stock.",
                accent="blue",
            )
        )

        self.stats_row.addWidget(
            _card(
                "Crops Stock",
                f"{_fmt_num(stock.get('crops_bags'))} bags",
                f"Total weight: {_fmt_num(stock.get('crops_kg'))} kg",
                accent="teal",
            )
        )

        # ========== ADVANCES TAB ==========
        advance_in = advances.get("advance_in_remaining", 0)
        advance_out = advances.get("advance_out_remaining", 0)

        self.advances_row_top.addWidget(
            _card(
                "Advance IN",
                _fmt_money(advance_in),
                "Customer prefunded balances remaining.",
                accent="amber",
                on_click=lambda: self._open_advance_settlement("IN"),
            )
        )

        self.advances_row_top.addWidget(
            _card(
                "Advance OUT",
                _fmt_money(advance_out),
                "Supplier prefunded balances remaining.",
                accent="orange",
                on_click=lambda: self._open_advance_settlement("OUT"),
            )
        )
        
        # ========== PAYMENTS TAB (NEW SETTLEMENT MODEL) ==========
        # Two separate calls, deliberately: "Partially Paid"/"Unpaid" are a
        # CURRENT outstanding-balance snapshot - an unpaid purchase from
        # yesterday is still unpaid today, so that stays all-time. "Fully
        # Paid" is genuine daily THROUGHPUT (how much did we fully collect
        # in this period) - that one resets to today by default and follows
        # the date filter above, per the client's explicit request.
        payment_snap_all = get_purchase_payment_snapshot(role="admin", user_id=None, scope="all")
        payment_snap_paid = get_purchase_payment_snapshot(
            role="admin", user_id=None,
            start_date=self.paid_start_date, end_date=self.paid_end_date)
        summary = payment_snap_all.get("summary") or {}
        paid_summary = payment_snap_paid.get("summary") or {}

        paid = paid_summary.get("paid") or {}
        partial = summary.get("partial") or {}
        unpaid = summary.get("unpaid") or {}

        self.payments_row.addWidget(
            _card(
                "Fully Paid Purchases",
                f"{paid.get('count', 0)}",
                f"Total: {_fmt_money(paid.get('total', 0))}",
                accent="green",
                on_click=lambda: self._open_settlement("PAID"),
            )
        )

        self.payments_row.addWidget(
            _card(
                "Partially Paid",
                f"{partial.get('count', 0)}",
                f"Outstanding: {_fmt_money(partial.get('total_balance', 0))}",
                accent="amber",
                on_click=lambda: self._open_settlement("PARTIAL"),
            )
        )

        self.payments_row.addWidget(
            _card(
                "Unpaid Purchases",
                f"{unpaid.get('count', 0)}",
                f"Outstanding: {_fmt_money(unpaid.get('total_balance', 0))}",
                accent="red",
                on_click=lambda: self._open_settlement("UNPAID"),
            )
        )

        # ================= SALES SETTLEMENT =================
        # Same split as purchases above: Partial/Unpaid stay all-time
        # (current outstanding status), Fully Paid resets to today by
        # default and follows the same date filter.
        sale_snap_all = get_sale_payment_snapshot(role="admin", user_id=None, scope="all")
        sale_snap_paid = get_sale_payment_snapshot(
            role="admin", user_id=None,
            start_date=self.paid_start_date, end_date=self.paid_end_date)
        sale_summary = sale_snap_all.get("summary") or {}
        sale_paid_summary = sale_snap_paid.get("summary") or {}

        sales_paid = sale_paid_summary.get("paid") or {}
        sales_partial = sale_summary.get("partial") or {}
        sales_unpaid = sale_summary.get("unpaid") or {}

        self.sales_row.addWidget(
            _card(
                "Fully Paid Sales",
                f"{sales_paid.get('count', 0)}",
                f"Total: {_fmt_money(sales_paid.get('total', 0))}",
                accent="green",
                on_click=lambda: self._open_sale_settlement("PAID"),
            )
        )

        self.sales_row.addWidget(
            _card(
                "Partially Paid Sales",
                f"{sales_partial.get('count', 0)}",
                f"Outstanding: {_fmt_money(sales_partial.get('total_balance', 0))}",
                accent="amber",
                on_click=lambda: self._open_sale_settlement("PARTIAL"),
            )
        )

        self.sales_row.addWidget(
            _card(
                "Unpaid Sales",
                f"{sales_unpaid.get('count', 0)}",
                f"Outstanding: {_fmt_money(sales_unpaid.get('total_balance', 0))}",
                accent="red",
                on_click=lambda: self._open_sale_settlement("UNPAID"),
            )
        )

        # ========== PARTY BALANCES TAB ==========
        self._refresh_party_balances()

        # ========== TOP CROPS ==========
        self.top_table.setRowCount(0)
        for r in top:
            row = self.top_table.rowCount()
            self.top_table.insertRow(row)

            self.top_table.setItem(row, 0, QTableWidgetItem(str(r.get("name") or "")))
            self.top_table.setItem(row, 1, QTableWidgetItem(str(r.get("sku") or "")))

            kg_item = QTableWidgetItem(_fmt_num(r.get("net_kg")))
            kg_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.top_table.setItem(row, 2, kg_item)

    # =============================
    # PARTY BALANCES (Unpaid + Partial, split Purchases vs Sales)
    # =============================
    def _refresh_party_balances(self):
        # Reuses the same all-time snapshots the Payments tab cards already
        # pull from - "Unpaid"/"Partial" are current outstanding status,
        # so this stays all-time regardless of the Fully-Paid date filter
        # above (same reasoning as the cards themselves).
        purchase_snap = get_purchase_payment_snapshot(role="admin", user_id=None, scope="all")
        sale_snap = get_sale_payment_snapshot(role="admin", user_id=None, scope="all")

        purchase_rows = purchase_snap.get("rows") or []
        sale_rows = sale_snap.get("rows") or []

        # 🚧 Purchases and Sales exposure are accumulated into two fully
        # separate dicts and rendered into two separate tables - never
        # merged or netted together, since "suppliers we pay" and
        # "customers who pay us" answer two different questions and
        # mixing them into one number hides which side actually needs
        # follow-up.
        suppliers: dict = {}
        customers: dict = {}

        def _bucket(store: dict, name: str) -> dict:
            key = (str(name or "").strip()) or "Unknown Party"
            if key not in store:
                store[key] = {"balance": 0.0, "docs": 0}
            return store[key]

        for r in purchase_rows:
            status = str(r.get("status") or "").upper()
            if status not in ("UNPAID", "PARTIAL"):
                continue
            b = _bucket(suppliers, r.get("supplier"))
            b["balance"] += float(r.get("balance") or 0)
            b["docs"] += 1

        for r in sale_rows:
            status = str(r.get("status") or "").upper()
            if status not in ("UNPAID", "PARTIAL"):
                continue
            b = _bucket(customers, r.get("customer"))
            b["balance"] += float(r.get("balance") or 0)
            b["docs"] += 1

        bold_font = QFont()
        bold_font.setBold(True)

        # ---- Suppliers we pay (Purchases) ----
        suppliers_ordered = sorted(
            suppliers.items(), key=lambda kv: kv[1]["balance"], reverse=True
        )

        self.balances_purchase_table.setRowCount(0)
        grand_purchase = 0.0

        for name, data in suppliers_ordered:
            balance = float(data["balance"] or 0)
            grand_purchase += balance

            row = self.balances_purchase_table.rowCount()
            self.balances_purchase_table.insertRow(row)

            name_item = QTableWidgetItem(name)
            name_item.setFont(bold_font)
            self.balances_purchase_table.setItem(row, 0, name_item)

            docs_item = QTableWidgetItem(str(data["docs"]))
            docs_item.setTextAlignment(Qt.AlignCenter)
            self.balances_purchase_table.setItem(row, 1, docs_item)

            bal_item = QTableWidgetItem(_fmt_money(balance))
            bal_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bal_item.setForeground(Qt.red)
            bal_item.setFont(bold_font)
            self.balances_purchase_table.setItem(row, 2, bal_item)

        self.balances_purchase_footer.setText(
            f"Total We Pay: {_fmt_money(grand_purchase)}    |    "
            f"Suppliers With Balance: {len(suppliers_ordered)}"
        )

        # ---- Customers who pay us (Sales) ----
        customers_ordered = sorted(
            customers.items(), key=lambda kv: kv[1]["balance"], reverse=True
        )

        self.balances_sale_table.setRowCount(0)
        grand_sale = 0.0

        for name, data in customers_ordered:
            balance = float(data["balance"] or 0)
            grand_sale += balance

            row = self.balances_sale_table.rowCount()
            self.balances_sale_table.insertRow(row)

            name_item = QTableWidgetItem(name)
            name_item.setFont(bold_font)
            self.balances_sale_table.setItem(row, 0, name_item)

            docs_item = QTableWidgetItem(str(data["docs"]))
            docs_item.setTextAlignment(Qt.AlignCenter)
            self.balances_sale_table.setItem(row, 1, docs_item)

            bal_item = QTableWidgetItem(_fmt_money(balance))
            bal_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bal_item.setForeground(Qt.darkGreen)
            bal_item.setFont(bold_font)
            self.balances_sale_table.setItem(row, 2, bal_item)

        self.balances_sale_footer.setText(
            f"Total They Pay Us: {_fmt_money(grand_sale)}    |    "
            f"Customers With Balance: {len(customers_ordered)}"
        )

    def _open_settlement(self, status: str):
        dlg = PurchaseSettlementDialog(
            status=status,
            role="admin",
            user_id=None,
            current_user_id=self.current_user_id,
            on_change=self.refresh,
            parent=self
        )
        dlg.exec_()

    def _open_sale_settlement(self, status: str):
        dlg = SaleSettlementDialog(
            status=status,
            role="admin",
            user_id=None,
            current_user_id=self.current_user_id,
            on_change=self.refresh,
            parent=self
        )
        dlg.exec_()

    def _open_advance_settlement(self, direction: str):
        from apps.product_management_app.features.admin_dashboard.views.advance_settlement_dialog import AdvanceSettlementDialog
        dlg = AdvanceSettlementDialog(
            direction=direction,
            role="admin",
            user_id=None,
            current_user_id=self.current_user_id,
            on_change=self.refresh,
            parent=self
        )
        dlg.exec_()