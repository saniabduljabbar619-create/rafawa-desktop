# apps/product_management_app/features/profiling/views/customer_profile.py

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QTextEdit,
    QTableWidget, QTableWidgetItem, QMessageBox, QPushButton, QGroupBox,
    QDialog, QFormLayout, QComboBox, QRadioButton, QLineEdit, QCheckBox, QDateEdit
)

import re
from datetime import date as _date, datetime as _dt
from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import QFrame
from PySide2.QtGui import QColor, QFont
from typing import Optional

from database.services.party_balance_service import PartyBalanceService
from shared.uix.widgets.cards import avatar, chip
from apps.product_management_app.app_state import state
from shared.config.constants import LEDGER_CROPS, LEDGER_FERTILIZERS
from apps.product_management_app.features.receipts_v2 import (
    print_receipt, print_full_then_optional_delivery, PURCHASE, ADVANCE, MODE_FULL,
)


_PROFILE_QSS = """
#ProfileBanner { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0b2a24, stop:1 #115e59); border-radius:14px; }
#ProfileBanner QLabel { background:transparent; }
#ProfileName { color:#ffffff; font-size:22px; font-weight:700; }
#ProfileMeta { color:rgba(255,255,255,0.72); font-size:12px; }
#ProfileBalCaption { color:rgba(255,255,255,0.6); font-size:10px; font-weight:700; letter-spacing:2px; }
#ProfileBalance { color:#ffffff; font-size:26px; font-weight:700; }
#SumGroup { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px; }
#SumHeading { color:#6b7280; font-size:10px; font-weight:700; letter-spacing:1px; background:transparent; }
#SumCaption { color:#9ca3af; font-size:11px; background:transparent; }
QTabWidget#ProfileTabs::pane { border:none; border-top:1px solid #e5e7eb; background:transparent; }
QTabWidget#ProfileTabs QTabBar::tab { background:transparent; color:#6b7280; padding:9px 14px; border:none;
    border-bottom:2px solid transparent; font-weight:600; }
QTabWidget#ProfileTabs QTabBar::tab:hover { color:#111827; }
QTabWidget#ProfileTabs QTabBar::tab:selected { color:#0f766e; border-bottom:2px solid #0f766e; }
#ProfileRoot QTableWidget { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px;
    gridline-color:transparent; alternate-background-color:#f9fafb; outline:0; }
#ProfileRoot QTableWidget::item { padding:4px 8px; border-bottom:1px solid #f1f5f9; }
#ProfileRoot QTableWidget::item:selected { background:#ecfdf5; color:#065f46; }
#ProfileRoot QTableWidget QHeaderView::section { background:#f8fafc; color:#6b7280; border:none;
    border-bottom:1px solid #e5e7eb; padding:8px; font-size:11px; font-weight:700; }
"""


class CustomerProfileView(QWidget):

    def __init__(self, customer=None, mode: str = "admin", walkin_name: Optional[str] = None):
        super().__init__()

        self.customer = customer
        self.walkin_name = walkin_name
        self.session_user_id = state.require_user_id()
        self.mode = mode

        self.setObjectName("ProfileRoot")
        self.setStyleSheet(_PROFILE_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # ---------------------------------------------------------
        # HEADER BANNER
        # ---------------------------------------------------------
        banner = QFrame()
        banner.setObjectName("ProfileBanner")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.setSpacing(16)

        display_name = customer.full_name if customer else f"{walkin_name}"
        bl.addWidget(avatar(display_name, 56, on_dark=True))

        ident = QVBoxLayout()
        ident.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        title = QLabel(display_name)
        title.setObjectName("ProfileName")
        name_row.addWidget(title)
        if customer:
            active = str(customer.status).lower() == "active"
            name_row.addWidget(chip("Active" if active else str(customer.status).title(),
                                    "green" if active else "amber"))
            if getattr(customer, "is_supplier", False):
                name_row.addWidget(chip("Also a Supplier", "blue"))
        else:
            name_row.addWidget(chip("Walk-in Trader", "amber"))
        name_row.addStretch(1)
        ident.addLayout(name_row)
        if customer:
            bits = [f"ID {customer.id}"]
            if customer.phone:
                bits.append(customer.phone)
            if customer.email:
                bits.append(customer.email)
            if customer.address:
                bits.append(customer.address)
            meta = QLabel("   -   ".join(bits))
        else:
            meta = QLabel("No permanent account - transactions are grouped by name")
        meta.setObjectName("ProfileMeta")
        ident.addWidget(meta)
        bl.addLayout(ident, 1)

        bal_box = QVBoxLayout()
        bal_box.setSpacing(0)
        cap = QLabel("OWES US")
        cap.setObjectName("ProfileBalCaption")
        cap.setAlignment(Qt.AlignRight)
        self.banner_balance = QLabel("0.00")
        self.banner_balance.setObjectName("ProfileBalance")
        self.banner_balance.setAlignment(Qt.AlignRight)
        bal_box.addWidget(cap)
        bal_box.addWidget(self.banner_balance)
        bl.addLayout(bal_box)

        self.btn_new_activity = QPushButton("+  New Activity")
        self.btn_new_activity.setObjectName("PrimaryButton")
        self.btn_new_activity.setMinimumHeight(36)
        bl.addWidget(self.btn_new_activity, 0, Qt.AlignVCenter)
        if self.mode == "cashier":
            self.btn_new_activity.setVisible(False)
        self.btn_new_activity.clicked.connect(self.new_activity)
        layout.addWidget(banner)

        # ---------------------------------------------------------
        # SUMMARY BAR
        # ---------------------------------------------------------
        self._build_summary_bar(layout)

        # ---------------------------------------------------------
        # TABS SCAFFOLDING
        # ---------------------------------------------------------
        tabs = QTabWidget()
        tabs.setObjectName("ProfileTabs")
        tabs.setDocumentMode(True)
        # the tab bar sizes its tabs with its own font, not the stylesheet's bold one - give it the same weight
        # or long labels ("Sales/Purchases", "Transaction history") are clipped at both ends
        _f = tabs.tabBar().font()
        _f.setWeight(QFont.DemiBold)
        tabs.tabBar().setFont(_f)
        self.tabs = tabs
        layout.addWidget(tabs, 1)

        # 1. Activity Tab
        self.activity_start_date: Optional[object] = None
        self.activity_end_date: Optional[object] = None

        activity = QWidget()
        av = QVBoxLayout(activity)
        (
            self.activity_date_chk, self.activity_d_from, self.activity_d_to,
        ) = self._add_date_filter_row(av, self._on_activity_dates_changed)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self.btn_export_activity = QPushButton("Export to Excel")
        self.btn_export_activity.clicked.connect(self._export_activity_to_excel)
        export_row.addWidget(self.btn_export_activity)
        av.addLayout(export_row)

        self.activity_table = self._make_activity_table()
        av.addWidget(self.activity_table, 1)
        # the full story of whichever row is selected (advances and payments carry every detail)
        self.activity_detail = QLabel("Click a row to read everything about it here.")
        self.activity_detail.setWordWrap(True)
        self.activity_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.activity_detail.setMinimumHeight(86)
        self.activity_detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.activity_detail.setStyleSheet(
            "background:#f6f7fb; border:1px solid #dfe3ee; border-radius:6px; padding:8px; color:#1c2333;")
        av.addWidget(self.activity_detail)
        self.activity_table.currentCellChanged.connect(self._show_activity_detail)
        tabs.addTab(activity, "Activity")

        # 2. Crops Tab
        crops_tab = QWidget()
        cv = QVBoxLayout(crops_tab)
        self.crops_table = self._make_crops_table()
        cv.addWidget(self.crops_table)
        tabs.addTab(crops_tab, "Crops")

        # 3. Fertilizers Tab
        fert_tab = QWidget()
        fv = QVBoxLayout(fert_tab)
        self.fertilizers_table = self._make_fertilizers_table()
        fv.addWidget(self.fertilizers_table)
        tabs.addTab(fert_tab, "Fertilizers")

        # 4. Returns Tab
        returns_tab = QWidget()
        rv = QVBoxLayout(returns_tab)

        self.returns_table = self._make_returns_table()
        rv.addWidget(self.returns_table)
        tabs.addTab(returns_tab, "Returns")
        
        # 5. Payments Tab
        self.payments_start_date: Optional[object] = None
        self.payments_end_date: Optional[object] = None

        pay_tab = QWidget()
        pv = QVBoxLayout(pay_tab)
        pv.setSpacing(10)

        (
            self.payments_date_chk, self.payments_d_from, self.payments_d_to,
        ) = self._add_date_filter_row(pv, self._on_payments_dates_changed)

        top = QHBoxLayout()
        pv.addLayout(top)

        self.btn_make_payment = QPushButton("Make Payment")
        self.btn_make_payment.setMinimumHeight(32)
        self.btn_advance_pay = QPushButton("Advance Pay Center")
        self.btn_advance_pay.setMinimumHeight(32)

        top.addWidget(self.btn_make_payment)
        top.addWidget(self.btn_advance_pay)
        top.addStretch(1)

        self.payments_table = QTableWidget(0, 10)
        self.payments_table.setHorizontalHeaderLabels([
            "Date", "Type", "Reference", "Direction", "Amount",
            "Balance Before", "Balance After", "Method", "Operator", "Notes"
        ])
        self.payments_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.payments_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.payments_table.horizontalHeader().setStretchLastSection(True)
        self.payments_table.setAlternatingRowColors(True)
        pv.addWidget(self.payments_table)
        tabs.addTab(pay_tab, "Payments")
        
        # 6. Advances Tab
        adv_tab = QWidget()
        av = QVBoxLayout(adv_tab)
        av.setSpacing(10)

        adv_top = QHBoxLayout()
        self.btn_void_advance = QPushButton("Void Selected Advance")
        self.btn_void_advance.setMinimumHeight(32)
        adv_top.addWidget(self.btn_void_advance)
        adv_top.addStretch(1)
        av.addLayout(adv_top)

        self.advances_table = self._make_advances_table()
        av.addWidget(self.advances_table)
        tabs.addTab(adv_tab, "Advances")

        # 7. Revert Mistakes Tab (admin only - reverting money has real
        # financial risk, so this stays out of the cashier workspace
        # entirely rather than just being disabled there)
        if self.mode != "cashier":
            self._build_revert_tab(tabs)

        # Record-by-record tabs (registered customers only): Sales/Purchases (two sides, tick to swap),
        # Products, Transaction history
        self.tab_docs = self.tab_sales = self.tab_purchases = self.tab_products = self.tab_history = None
        if self.customer:
            from apps.product_management_app.features.profiling.views.records_tabs import (
                HistoryTab, ProductsTab, SalesPurchasesTab)
            pid = int(self.customer.id)
            self.tab_docs = SalesPurchasesTab(pid)
            self.tab_purchases, self.tab_sales = self.tab_docs.purchases, self.tab_docs.sales
            self.tab_products, self.tab_history = ProductsTab(pid), HistoryTab(pid)
            tabs.setElideMode(Qt.ElideNone)
            for i, (w, label) in enumerate(((self.tab_docs, "Sales/Purchases"), (self.tab_products, "Products"),
                                            (self.tab_history, "Transaction history")), start=1):
                tabs.insertTab(i, w, label)
            self.tab_docs.swap_requested.connect(self.open_swap)
            self.tab_docs.changed.connect(self.refresh_all)      # a price was edited: every tab follows

        # Connect Signal Slots
        self.btn_make_payment.clicked.connect(self.open_payment_router)
        self.btn_advance_pay.clicked.connect(self.open_advance_center)
        if not self.customer:
            self.btn_advance_pay.setVisible(False)   # a walk-in has no account to hold an advance

        self.activity_table.cellDoubleClicked.connect(self._preview_document)
        self.crops_table.cellDoubleClicked.connect(self._preview_document)
        self.fertilizers_table.cellDoubleClicked.connect(self._preview_document)
        self.returns_table.cellDoubleClicked.connect(self._preview_document)
        self.payments_table.cellDoubleClicked.connect(self._preview_document)
        self.advances_table.cellDoubleClicked.connect(self._preview_advance)
        self.btn_void_advance.clicked.connect(self._void_selected_advance)

        self.refresh_all()

    # ---------------------------------------------------------
    # 🔒 EXPLICIT REAL-TIME OPERATOR LOOKUP ENGINE
    # ---------------------------------------------------------
    def _fetch_operator_by_reference(self, ref_str: str) -> str:
        """
        Queries the database to map table rows to the actual cashier's username.
        """
        ref_str = str(ref_str or "").upper().strip()
        if not ref_str:
            return "System"

        # One lookup per reference per refresh: the activity/crops/fertilizer/
        # returns tabs (and the Excel export) used to issue 1-2 queries for
        # EVERY row. The cache is cleared in refresh_all().
        cache = self.__dict__.setdefault("_operator_cache", {})
        if ref_str in cache:
            return cache[ref_str]
        cache[ref_str] = result = self._lookup_operator(ref_str)
        return result

    def _lookup_operator(self, ref_str: str) -> str:
        match = re.match(r"^([A-Z\-]+)-(\d+)$", ref_str)
        if not match:
            return "System"

        prefix, entity_id_str = match.group(1), match.group(2)
        entity_id = int(entity_id_str)

        if prefix in ("SAL", "INV"):
            table_name = "sales"
        elif prefix == "PUR":
            table_name = "purchases"
        elif prefix in ("PAY", "ADV-PAY"):
            table_name = "payments"
        elif prefix == "ADV":
            table_name = "advances"
        elif prefix in ("RET", "SRET"):
            table_name = "sale_returns"
        elif prefix == "PRET":
            table_name = "purchase_returns"
        else:
            return "System"

        
        # ... top half of your _fetch_operator_by_reference remains unchanged ...
        from database.session import get_session
        try:
            with get_session() as s:
                row = s.connection().exec_driver_sql(
                    f"""
                    SELECT u.username 
                    FROM {table_name} t 
                    JOIN users u ON t.created_by_user_id = u.id 
                    WHERE t.id = %s
                    """, (entity_id,)
                ).fetchone()

                if not row:
                    return "System"

                creator_name = str(row[0] or "").strip() or "System"

                # Only sales/purchases can genuinely have more than one
                # cashier's money against them (an advance/return doesn't
                # have its own separate payment stream the same way) - so
                # this only ever runs for those two table names, leaving
                # every other reference type completely unchanged.
                if table_name not in ("sales", "purchases"):
                    return creator_name

                entity_type = "sale" if table_name == "sales" else "purchase"
                payer_rows = s.connection().exec_driver_sql(
                    """
                    SELECT DISTINCT u.username
                    FROM payments p
                    JOIN users u ON p.created_by_user_id = u.id
                    WHERE p.entity_type = %s AND p.entity_id = %s
                      AND p.status != 'voided'
                    """, (entity_type, entity_id)
                ).fetchall()
                payer_names = sorted({str(r[0]).strip() for r in payer_rows if r and r[0]})

                # Only the creator paid (or nobody's paid yet) - the
                # simple, single-name case, unchanged from before.
                if not payer_names or payer_names == [creator_name]:
                    return creator_name

                # More than one cashier's money is genuinely behind this
                # row - name all of them, so the gap this exists to fix
                # (two cashiers splitting a single payment) is visible
                # right in the Activity tab, not hidden behind one name.
                others = [n for n in payer_names if n != creator_name]
                if creator_name in payer_names:
                    return f"{creator_name} + {', '.join(others)}" if others else creator_name
                return f"{creator_name} (recorded); paid by {', '.join(payer_names)}"
        except Exception:
            return "System"

    # ---------------------------------------------------------
    # TABLE LAYOUT CONSTRUCTORS
    # ---------------------------------------------------------
    def _format_dt_bankstyle(self, dt) -> str:
        """
        Bank-statement style date/time, e.g. "Tuesday 25 August 2026 -
        03:45 PM". Used only by the Activity and Payments tabs. Accepts
        a real datetime (the normal case) or a string (older/raw-SQL
        walk-in rows); falls back to the raw value if it can't parse.
        """
        if not dt:
            return ""
        if isinstance(dt, str):
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = _dt.strptime(dt[:19], fmt)
                    break
                except Exception:
                    continue
            if parsed is None:
                return dt
            dt = parsed
        try:
            return dt.strftime("%A %d %B %Y - %I:%M %p")
        except Exception:
            return str(dt)

    def _format_qty(self, bags, kg) -> str:
        bags = int(bags or 0)
        kg = float(kg or 0)
        if bags > 0 and kg > 0:
            return f"{bags} bag(s) ({kg:,.0f} kg)"
        if bags > 0:
            return f"{bags} bag(s)"
        if kg > 0:
            return f"{kg:,.0f} kg"
        return ""

    def _make_activity_table(self) -> QTableWidget:
        t = QTableWidget(0, 12)
        t.setHorizontalHeaderLabels([
            "Date", "Ledger", "Type", "Reference", "Summary",
            "Total", "Paid", "Balance", "Balance Before", "Balance After",
            "Status", "Operator",
        ])
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True)
        return t

    def _make_crops_table(self) -> QTableWidget:
        t = QTableWidget(0, 9)
        t.setHorizontalHeaderLabels([
            "Date", "Product", "Type", "Reference", "Quantity", "Unit Rate", "Amount", "Operator", "Status"
        ])
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True)
        return t

    def _make_fertilizers_table(self) -> QTableWidget:
        t = QTableWidget(0, 9)
        t.setHorizontalHeaderLabels([
            "Date", "Product", "Type", "Reference", "Quantity", "Unit Rate", "Amount", "Operator", "Status"
        ])
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True)
        return t

    def _make_returns_table(self) -> QTableWidget:
        t = QTableWidget(0, 9)
        t.setHorizontalHeaderLabels([
            "Date", "Ledger", "Type", "Reference", "Quantity", "Amount", "Reason", "Operator", "Status"
        ])
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _make_advances_table(self) -> QTableWidget:
        t = QTableWidget(0, 9)
        t.setHorizontalHeaderLabels([
            "Date", "Direction", "Type", "Amount", "Applied", "Remaining", "Status", "Operator", "Notes"
        ])
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True)
        return t

    # ---------------------------------------------------------
    # DATE-RANGE FILTER (shared row builder for Activity + Payments tabs)
    # ---------------------------------------------------------
    def _add_date_filter_row(self, parent_layout, on_change):
        """
        Unchecked (default) = show full history, unfiltered - this is a
        profile the operator browses, not a daily-reset dashboard card, so
        nothing narrows until they explicitly ask for a range.
        """
        row = QHBoxLayout()
        chk = QCheckBox("Filter by date range")
        chk.stateChanged.connect(on_change)
        row.addWidget(chk)

        d_from = QDateEdit()
        d_from.setCalendarPopup(True)
        d_from.setDisplayFormat("yyyy-MM-dd")
        d_from.setDate(QDate.currentDate())
        d_from.setEnabled(False)
        d_from.dateChanged.connect(on_change)
        row.addWidget(d_from)

        row.addWidget(QLabel("to"))

        d_to = QDateEdit()
        d_to.setCalendarPopup(True)
        d_to.setDisplayFormat("yyyy-MM-dd")
        d_to.setDate(QDate.currentDate())
        d_to.setEnabled(False)
        d_to.dateChanged.connect(on_change)
        row.addWidget(d_to)

        row.addStretch(1)
        parent_layout.addLayout(row)
        return chk, d_from, d_to

    def _on_activity_dates_changed(self):
        on = self.activity_date_chk.isChecked()
        self.activity_d_from.setEnabled(on)
        self.activity_d_to.setEnabled(on)
        if on:
            self.activity_start_date = self.activity_d_from.date().toPython()
            self.activity_end_date = self.activity_d_to.date().toPython()
        else:
            self.activity_start_date = None
            self.activity_end_date = None
        self.load_activity_table()

    def _on_payments_dates_changed(self):
        on = self.payments_date_chk.isChecked()
        self.payments_d_from.setEnabled(on)
        self.payments_d_to.setEnabled(on)
        if on:
            self.payments_start_date = self.payments_d_from.date().toPython()
            self.payments_end_date = self.payments_d_to.date().toPython()
        else:
            self.payments_start_date = None
            self.payments_end_date = None
        self.load_payments_table()

    # ---------------------------------------------------------
    # DYNAMIC LEDGER DATA HYDRATION
    # ---------------------------------------------------------
    def _export_activity_to_excel(self):
        from PySide2.QtWidgets import QFileDialog, QMessageBox
        from database.services.party_ledger_service import PartyLedgerService
        from apps.product_management_app.features.profiling.activity_export import export_activity_to_excel

        party_name = self.customer.full_name if self.customer else (self.walkin_name or "Walk-in")
        default_name = f"Activity_{party_name}".replace(" ", "_") + ".xlsx"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Activity to Excel", default_name, "Excel Files (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        # Reuses the exact same data source load_activity_table() itself
        # already uses, so the export always matches what's on screen -
        # never a separate, potentially-drifted query.
        if self.customer:
            acts = PartyLedgerService.list_activity_details(
                party_id=int(self.customer.id),
                start_date=self.activity_start_date,
                end_date=self.activity_end_date,
            )
        else:
            acts = PartyLedgerService.list_walkin_activity(walkin_name=self.walkin_name)

        try:
            export_activity_to_excel(
                acts, party_name, path,
                operator_lookup=self._fetch_operator_by_reference,
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
            return

        QMessageBox.information(self, "Export Complete", f"Activity exported to:\n{path}")

    def load_activity_table(self):
        from database.services.party_ledger_service import PartyLedgerService

        if self.customer:
            acts = PartyLedgerService.list_activity_details(
                party_id=int(self.customer.id),
                start_date=self.activity_start_date,
                end_date=self.activity_end_date,
            )
        else:
            # Walk-in activity never had a separate payment stream to fold
            # in to begin with (only sale/purchase rows), so it's still
            # the older "amount" shape and isn't date-filterable here yet.
            acts = PartyLedgerService.list_walkin_activity(walkin_name=self.walkin_name)

        type_color = {
            "sale": QColor(30, 140, 70), "purchase": QColor(200, 60, 60),
            "advance": QColor(140, 80, 170),
            "sale_return": QColor(220, 140, 40), "purchase_return": QColor(220, 140, 40),
            "payment": QColor(90, 110, 200),
        }
        status_colors = {
            "paid": QColor(30, 140, 70), "partial": QColor(200, 140, 20), "unpaid": QColor(200, 60, 60),
        }

        self.activity_table.setRowCount(0)
        for a in acts:
            r = self.activity_table.rowCount()
            self.activity_table.insertRow(r)

            if a.get("type") == "settlement_marker":
                text = str(a.get("summary", ""))
                display = f"---- {text} ----"

                it = QTableWidgetItem(display)
                it.setTextAlignment(Qt.AlignCenter)
                f = QFont()
                f.setBold(True)
                f.setPointSize(f.pointSize() + 1)
                it.setFont(f)
                it.setForeground(QColor(30, 140, 70))
                self.activity_table.setItem(r, 0, it)
                self.activity_table.setSpan(r, 0, 1, 12)
                continue

            ref_str = a.get("reference", "")
            # Payment rows already carry their own operator directly (the
            # reference-parsing lookup below can't handle "Payment -
            # SAL-1" style references) - every other row type keeps using
            # the existing, unchanged lookup.
            if a.get("type") == "payment" and a.get("operator"):
                operator = a["operator"]
            else:
                operator = self._fetch_operator_by_reference(ref_str)

            # "total" is the new shape carried by every row (sale, purchase,
            # return, advance all use it now); "amount" is the older shape
            # still returned by list_walkin_activity above.
            total_val = a.get("total")
            if total_val is None:
                total_val = a.get("amount")
            total_val = float(total_val or 0)

            paid_val = a.get("paid")
            balance_val = a.get("balance")
            bal_before = a.get("balance_before")
            bal_after = a.get("balance_after")
            status = str(a.get("status", "") or "")

            cols = [
                self._format_dt_bankstyle(a.get("dt")),
                a.get("ledger", ""),
                ("   \u21b3 " if a.get("type") == "payment" else "") + str(a.get("type", "")),
                ref_str,
                a.get("summary", ""),
                f"{total_val:,.2f}",
                f"{float(paid_val):,.2f}" if paid_val is not None else "",
                f"{float(balance_val):,.2f}" if balance_val is not None else "",
                f"{float(bal_before):,.2f}" if bal_before is not None else "",
                f"{float(bal_after):,.2f}" if bal_after is not None else "",
                status.capitalize(),
                operator,
            ]

            row_color = type_color.get(str(a.get("type", "")).lower())
            detail = str(a.get("detail") or "")
            if not detail:
                detail = "\n".join(x for x in (str(a.get("summary") or ""),
                                              f"Items: {a['items']}" if a.get("items") else "") if x)

            for c, val in enumerate(cols):
                it = QTableWidgetItem(str(val))
                if detail and c in (0, 4):
                    it.setToolTip(detail)
                if c == 0:
                    it.setData(Qt.UserRole + 1, detail)
                if c in (5, 6, 7, 8, 9):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

                if c == 5:
                    it.setForeground(QColor(200, 60, 60) if total_val < 0 else QColor(30, 140, 70))
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)
                elif c == 10 and status.lower() in status_colors:
                    it.setForeground(status_colors[status.lower()])
                elif row_color:
                    it.setForeground(row_color)

                self.activity_table.setItem(r, c, it)

    def _show_activity_detail(self, row, _col, _prev_row=-1, _prev_col=-1) -> None:
        item = self.activity_table.item(row, 0) if row >= 0 else None
        text = item.data(Qt.UserRole + 1) if item else ""
        self.activity_detail.setText(str(text or "Click a row to read everything about it here."))

    def load_crops_table(self):
        from database.services.party_ledger_service import PartyLedgerService

        if self.customer:
            rows = PartyLedgerService.list_crop_activity(party_id=int(self.customer.id))
        else:
            rows = PartyLedgerService.list_walkin_crop_activity(walkin_name=self.walkin_name)

        self.crops_table.setRowCount(0)
        for a in rows:
            r = self.crops_table.rowCount()
            self.crops_table.insertRow(r)

            qty = self._format_qty(a.get("bags"), a.get("kg"))
            amt_val = float(a.get("amount") or 0)
            ref_str = a.get("reference", "")
            
            operator = self._fetch_operator_by_reference(ref_str)

            cols = [
                str(a.get("dt") or "")[:19],
                f"Crops ({a.get('product','')})",
                a.get("type",""),
                ref_str,
                qty,
                f"{float(a.get('unit_rate') or 0):,.2f}",
                f"{amt_val:,.2f}",
                operator,  
                a.get("status","")
            ]

            for c, val in enumerate(cols):
                it = QTableWidgetItem(str(val))
                if c in (5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 6:
                    it.setForeground(QColor(200, 60, 60) if amt_val < 0 else QColor(30, 140, 70))
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)

                self.crops_table.setItem(r, c, it)

    def load_fertilizer_table(self):
        from database.services.party_ledger_service import PartyLedgerService

        if self.customer:
            rows = PartyLedgerService.list_fertilizer_activity(party_id=int(self.customer.id))
        else:
            rows = PartyLedgerService.list_walkin_fertilizer_activity(walkin_name=self.walkin_name)

        self.fertilizers_table.setRowCount(0)
        for a in rows:
            r = self.fertilizers_table.rowCount()
            self.fertilizers_table.insertRow(r)

            qty = self._format_qty(a.get("bags"), a.get("kg"))
            ref_str = a.get("reference", "")
            
            operator = self._fetch_operator_by_reference(ref_str)

            cols = [
                str(a.get("dt") or "")[:19],
                a.get("product_label",""),
                a.get("type",""),
                ref_str,
                qty,
                f"{float(a.get('unit_rate') or 0):,.2f}",
                f"{float(a.get('amount') or 0):,.2f}",
                operator,  
                a.get("status","")
            ]

            for c, val in enumerate(cols):
                it = QTableWidgetItem(str(val))
                if c in (5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.fertilizers_table.setItem(r, c, it)

    def load_returns_table(self):
        from database.services.party_ledger_service import PartyLedgerService

        if self.customer:
            rows = PartyLedgerService.list_return_activity(party_id=int(self.customer.id))
        else:
            rows = PartyLedgerService.list_walkin_return_activity(walkin_name=self.walkin_name)

        self.returns_table.setRowCount(0)
        for a in rows:
            r = self.returns_table.rowCount()
            self.returns_table.insertRow(r)

            qty = self._format_qty(a.get("bags"), a.get("kg"))
            amount_val = float(a.get("amount") or 0)
            ref_str = a.get("reference", "")
            
            operator = self._fetch_operator_by_reference(ref_str)

            cols = [
                str(a.get("dt") or "")[:19],
                a.get("ledger", ""),
                a.get("type", ""),
                ref_str,
                qty,
                f"{abs(amount_val):,.2f}",
                a.get("reason",""),
                operator,  
                a.get("status","")
            ]

            for c, val in enumerate(cols):
                it = QTableWidgetItem(str(val))
                if c == 5:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setForeground(QColor(200, 60, 60) if amount_val < 0 else QColor(30, 140, 70))
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)

                self.returns_table.setItem(r, c, it)

    def load_payments_table(self):
        from database.services.payment_service import PaymentService

        if self.customer:
            rows = PaymentService.list_party_payments(
                party_id=int(self.customer.id),
                created_by_user_id=self.session_user_id,
                start_date=self.payments_start_date,
                end_date=self.payments_end_date,
            )
        else:
            rows = PaymentService.list_walkin_payments(walkin_name=self.walkin_name)

        self.payments_table.setRowCount(0)
        for p in rows:
            r = self.payments_table.rowCount()
            self.payments_table.insertRow(r)

            amount_val = float(p.get("amount") or 0)
            entity_prefixes = {"sale": "SAL", "purchase": "PUR"}
            entity_prefix = entity_prefixes.get(str(p.get("entity_type", "")).lower(), str(p.get("entity_type", "")).upper())
            ref_prefix = f"{entity_prefix}-{p.get('entity_id','')}"
            ref = f"{ref_prefix} PAYMENT"

            bal_before = p.get("balance_before")
            bal_after = p.get("balance_after")

            # The payment's own, correctly-resolved operator - not
            # _fetch_operator_by_reference, which resolves who created
            # the SALE/PURCHASE itself, not who made THIS specific
            # payment. Every Payment row already carries its own real
            # created_by_user_id; list_party_payments now resolves it
            # directly rather than falling back to "System".
            operator = p.get("operator") or "System"

            cols = [
                self._format_dt_bankstyle(p.get("created_at")),
                p.get("entity_type",""),
                ref,
                p.get("direction",""),
                f"{amount_val:,.2f}",
                f"{float(bal_before):,.2f}" if bal_before is not None else "",
                f"{float(bal_after):,.2f}" if bal_after is not None else "",
                p.get("method",""),
                operator,
                p.get("notes","") or "",
            ]

            row_meta = {"entity_type": p.get("entity_type"), "entity_id": p.get("entity_id")}

            for c, val in enumerate(cols):
                it = QTableWidgetItem(str(val))
                if c in (4, 5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 4:
                    it.setForeground(QColor(200, 60, 60) if p.get("direction") == "out" else QColor(30, 140, 70))
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)

                it.setData(Qt.UserRole, row_meta)
                self.payments_table.setItem(r, c, it)

    def load_advances_table(self):
        from database.services.advance_service import AdvanceService
        self.advances_table.setRowCount(0)
        if not self.customer:
            return

        rows = AdvanceService.list_all_party_advances(party_id=int(self.customer.id))
        for a in rows:
            r = self.advances_table.rowCount()
            self.advances_table.insertRow(r)

            amount_val = float(a.get("amount") or 0)
            atype = a.get("advance_type", "")
            direction = a.get("direction", "")
            qty = self._format_qty(a.get("qty_bags"), a.get("total_weight_kg")) \
                if atype in ("crops", "fertilizer") else ""
            applied_val = float(a.get("applied") or 0)
            remaining_val = float(a.get("remaining") or 0)

            cols = [
                str(a.get("created_at") or "")[:19],
                direction,
                f"{atype} ({qty})" if qty else atype,
                f"{amount_val:,.2f}",
                f"{applied_val:,.2f}" if applied_val > 0.005 else "-",
                f"{remaining_val:,.2f}" if remaining_val > 0.005 else "Used up",
                a.get("status", ""),
                a.get("operator", ""),
                a.get("notes", "") or "",
            ]

            row_meta = {"entity_type": "advance", "entity_id": a.get("id")}
            is_void = str(a.get("status", "")).strip().lower() == "voided"

            for c, val in enumerate(cols):
                it = QTableWidgetItem(str(val))
                if c in (3, 4, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 3:
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)
                if is_void:
                    it.setForeground(QColor(150, 150, 150))
                elif c == 3:
                    # received = comes IN to us (green); given = goes OUT (red)
                    it.setForeground(QColor(30, 140, 70) if direction == "received" else QColor(200, 60, 60))
                it.setData(Qt.UserRole, row_meta)
                self.advances_table.setItem(r, c, it)

    # ---------------------------------------------------------
    # CORE INTERNALS & DIALOG ROUTERS

    # ---------------------------------------------------------
    # CORE INTERNALS & DIALOG ROUTERS
    # ---------------------------------------------------------
    def _preview_document(self, row, col):
        table = self.sender()
        ref = table.item(row, 3).text() if table.item(row, 3) else None
        item = table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None

        if meta:
            entity_type = meta.get("entity_type")
            entity_id = meta.get("entity_id")
        else:
            entity_type, entity_id = self._parse_reference(ref)

        if not entity_id:
            return

        from apps.product_management_app.features.profiling.dialogs.global_document_previewer import GlobalDocumentPreviewer
        GlobalDocumentPreviewer.open(parent=self, entity_type=entity_type, entity_id=entity_id)

    def _preview_advance(self, row, col):
        """Double-click an advance row to preview it on screen (no auto-print)."""
        item = self.advances_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        adv_id = int(meta.get("entity_id") or 0) if meta else 0
        if adv_id <= 0:
            return
        try:
            from apps.product_management_app.features.profiling.dialogs.global_document_previewer import GlobalDocumentPreviewer
            GlobalDocumentPreviewer.open(parent=self, entity_type=ADVANCE, entity_id=adv_id)
        except Exception as e:
            print("Advance preview failed:", e)

    def _void_selected_advance(self):
        from PySide2.QtWidgets import QInputDialog
        row = self.advances_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Void Advance", "Select an advance row first.")
            return

        item = self.advances_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        adv_id = int(meta.get("entity_id") or 0) if meta else 0
        if adv_id <= 0:
            return

        status_item = self.advances_table.item(row, 6)
        if status_item and status_item.text().strip().lower() == "voided":
            QMessageBox.information(self, "Void Advance", "This advance is already voided.")
            return

        confirm = QMessageBox.question(
            self, "Void Advance",
            "Void this advance? It stays in history (marked voided) but drops out of balances.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        reason, ok = QInputDialog.getText(self, "Void Reason", "Reason (optional):")
        if not ok:
            return

        from database.services.advance_service import AdvanceService
        try:
            AdvanceService.void_advance(adv_id, self.session_user_id, reason=reason or "")
            self.refresh_all()
        except Exception as e:
            QMessageBox.critical(self, "Void Failed", str(e))

    # ---------------------------------------------------------
    # REVERT MISTAKES (admin only)
    # ---------------------------------------------------------
    def _build_revert_tab(self, tabs: QTabWidget):
        revert_tab = QWidget()
        rmv = QVBoxLayout(revert_tab)
        rmv.setSpacing(10)

        warn = QLabel(
            "Undo a mistaken payment or advance. Voiding never edits or "
            "deletes the original record - it stays in history forever, "
            "but drops out of every balance immediately. To fix a wrong "
            "amount, void it here, then record a fresh, correct one."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "color:#92400e; background:#FFF6E5; border:1px solid #FFD79A;"
            " border-radius:8px; padding:8px;"
        )
        rmv.addWidget(warn)

        top = QHBoxLayout()
        top.addWidget(QLabel("Revert:"))
        self.revert_kind_combo = QComboBox()
        self.revert_kind_combo.addItem("Sale Payment", "sale")
        self.revert_kind_combo.addItem("Purchase Payment", "purchase")
        self.revert_kind_combo.addItem("Sale Transaction (no payment yet)", "sale_txn")
        self.revert_kind_combo.addItem("Purchase Transaction (no payment yet)", "purchase_txn")
        self.revert_kind_combo.addItem("Advance Settlement Payment", "advance")
        self.revert_kind_combo.addItem("Advance Recording", "advance_record")
        top.addWidget(self.revert_kind_combo)
        top.addStretch(1)
        self.btn_revert_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_revert_refresh)
        rmv.addLayout(top)

        self.revert_table = QTableWidget(0, 1)
        self.revert_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.revert_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.revert_table.verticalHeader().setVisible(False)
        self.revert_table.horizontalHeader().setStretchLastSection(True)
        self.revert_table.setAlternatingRowColors(True)
        rmv.addWidget(self.revert_table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_revert_selected = QPushButton("Revert Selected")
        self.btn_revert_selected.setMinimumHeight(32)
        self.btn_revert_selected.setStyleSheet(
            "background:#fee2e2; color:#991b1b; font-weight:700;"
        )
        footer.addWidget(self.btn_revert_selected)
        rmv.addLayout(footer)

        self.revert_kind_combo.currentIndexChanged.connect(self.load_revert_table)
        self.btn_revert_refresh.clicked.connect(self.load_revert_table)
        self.btn_revert_selected.clicked.connect(self._revert_selected)

        tabs.addTab(revert_tab, "Revert Mistakes")

    def load_revert_table(self):
        if not self.customer:
            # Walk-in traders share one placeholder party id across every
            # walk-in, so listing "their" payments here would actually mix
            # in every other walk-in's payments too - not safe to offer.
            self.revert_table.setColumnCount(1)
            self.revert_table.setHorizontalHeaderLabels(["Not available for walk-in traders"])
            self.revert_table.setRowCount(0)
            return

        kind = self.revert_kind_combo.currentData()
        self.revert_table.setSortingEnabled(False)

        if kind == "advance_record":
            headers = ["Date", "Direction", "Type", "Amount", "Quantity", "Status", "Operator", "Notes"]
            self.revert_table.setColumnCount(len(headers))
            self.revert_table.setHorizontalHeaderLabels(headers)

            from database.services.advance_service import AdvanceService
            rows = [
                a for a in AdvanceService.list_all_party_advances(party_id=int(self.customer.id))
                if str(a.get("status", "")).strip().lower() != "voided"
            ]

            self.revert_table.setRowCount(len(rows))
            for r, a in enumerate(rows):
                atype = a.get("advance_type", "")
                qty = self._format_qty(a.get("qty_bags"), a.get("total_weight_kg")) \
                    if atype in ("crops", "fertilizer") else ""
                created_at = a.get("created_at")

                date_item = QTableWidgetItem(str(created_at)[:19] if created_at else "")
                date_item.setData(Qt.UserRole, {"kind": "advance_record", "id": int(a.get("id") or 0)})
                self.revert_table.setItem(r, 0, date_item)

                cols = [
                    a.get("direction", ""), atype,
                    f"{float(a.get('amount') or 0):,.2f}", qty,
                    a.get("status", ""), a.get("operator", ""), a.get("notes", "") or "",
                ]
                for c, val in enumerate(cols, start=1):
                    it = QTableWidgetItem(str(val))
                    if c == 3:
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.revert_table.setItem(r, c, it)
        elif kind in ("sale_txn", "purchase_txn"):
            headers = ["Date", "Reference", "Amount", "Notes"]
            self.revert_table.setColumnCount(len(headers))
            self.revert_table.setHorizontalHeaderLabels(headers)

            if kind == "sale_txn":
                from database.services.sales_service import SaleService
                rows = SaleService.list_voidable_sales(party_id=int(self.customer.id))
            else:
                from database.services.purchase_service import PurchaseService
                rows = PurchaseService.list_voidable_purchases(party_id=int(self.customer.id))

            self.revert_table.setRowCount(len(rows))
            for r, t in enumerate(rows):
                created_at = t.get("created_at")

                date_item = QTableWidgetItem(str(created_at)[:19] if created_at else "")
                date_item.setData(Qt.UserRole, {"kind": kind, "id": int(t.get("id") or 0)})
                self.revert_table.setItem(r, 0, date_item)

                cols = [t.get("reference", ""), f"{float(t.get('amount') or 0):,.2f}", t.get("notes", "") or ""]
                for c, val in enumerate(cols, start=1):
                    it = QTableWidgetItem(str(val))
                    if c == 2:
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.revert_table.setItem(r, c, it)
        else:
            headers = ["Date", "Reference", "Direction", "Amount", "Method", "Notes"]
            self.revert_table.setColumnCount(len(headers))
            self.revert_table.setHorizontalHeaderLabels(headers)

            from database.services.payment_service import PaymentService
            rows = PaymentService.list_voidable_payments(
                party_id=int(self.customer.id), entity_type=kind
            )

            self.revert_table.setRowCount(len(rows))
            for r, p in enumerate(rows):
                created_at = p.get("created_at")

                date_item = QTableWidgetItem(str(created_at)[:19] if created_at else "")
                date_item.setData(Qt.UserRole, {"kind": kind, "id": int(p.get("id") or 0)})
                self.revert_table.setItem(r, 0, date_item)

                cols = [
                    p.get("reference", ""), p.get("direction", ""),
                    f"{float(p.get('amount') or 0):,.2f}",
                    p.get("method", "") or "", p.get("notes", "") or "",
                ]
                for c, val in enumerate(cols, start=1):
                    it = QTableWidgetItem(str(val))
                    if c == 3:
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.revert_table.setItem(r, c, it)

        self.revert_table.setSortingEnabled(True)

    def _revert_selected(self):
        if not self.customer:
            return

        row = self.revert_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select", "Select a row first.")
            return

        item = self.revert_table.item(row, 0)
        meta = item.data(Qt.UserRole) if item else None
        if not meta:
            return

        kind = meta.get("kind")
        item_id = int(meta.get("id") or 0)
        if item_id <= 0:
            return

        # Case C: an advance needs its settlement impact checked FIRST -
        # a fully-settled one can never be voided at all, a partially-
        # settled one needs an explicit, honest warning naming exactly
        # what else would be undone before any cascade is confirmed.
        cascade = False
        if kind == "advance_record":
            from database.services.advance_service import AdvanceService
            try:
                impact = AdvanceService.get_advance_settlement_impact(item_id)
            except Exception as e:
                QMessageBox.critical(self, "Revert Failed", str(e))
                return

            if impact["fully_settled"]:
                QMessageBox.warning(
                    self, "Cannot Void",
                    "This advance is fully settled - the money has genuinely, "
                    "entirely moved or been applied elsewhere. There is "
                    "nothing left here to safely undo."
                )
                return

            if impact["settled_total"] > 0.005:
                doc_lines = "\n".join(
                    f"  \u2022 {d['entity_type'].title()} #{d['entity_id']}: "
                    f"\u20a6{d['amount']:,.2f} would return to unpaid"
                    for d in impact["affected_documents"]
                )
                cascade_confirm = QMessageBox.warning(
                    self, "Partially Settled Advance",
                    f"This advance is partially settled "
                    f"(\u20a6{impact['settled_total']:,.2f} of "
                    f"\u20a6{impact['advance_amount']:,.2f}).\n\n"
                    f"Voiding it will ALSO reverse those settlements:\n\n"
                    f"{doc_lines}\n\n"
                    f"Continue?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if cascade_confirm != QMessageBox.Yes:
                    return
                cascade = True

        label = {
            "advance_record": "advance recording",
            "sale_txn": "sale transaction",
            "purchase_txn": "purchase transaction",
        }.get(kind, "payment")
        confirm = QMessageBox.question(
            self, "Revert Mistake",
            f"Revert this {label}? It stays in history (voided) but drops "
            f"out of every balance right away.\n\n"
            f"This does not itself un-revert - to correct a wrong amount, "
            f"record a fresh, correct entry afterward.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from PySide2.QtWidgets import QInputDialog
        reason, ok = QInputDialog.getText(self, "Reason (optional)", "Why is this being reverted?")
        if not ok:
            return

        from apps.product_management_app.app_state import events

        try:
            if kind == "advance_record":
                from database.services.advance_service import AdvanceService
                AdvanceService.void_advance(item_id, self.session_user_id, reason=reason or "", cascade=cascade)
            elif kind == "sale_txn":
                from database.services.sales_service import SaleService
                SaleService.void_sale(item_id, self.session_user_id, reason=reason or "")
            elif kind == "purchase_txn":
                from database.services.purchase_service import PurchaseService
                PurchaseService.void_purchase(item_id, self.session_user_id, reason=reason or "")
            else:
                from database.services.payment_service import PaymentService
                PaymentService.void_payment(item_id, self.session_user_id, reason=reason or "")
        except Exception as e:
            QMessageBox.critical(self, "Revert Failed", str(e))
            return

        events.money_changed.emit()
        QMessageBox.information(self, "Reverted", "Done - every balance has been updated.")
        self.refresh_all()

    def _parse_reference(self, ref):
        ref = str(ref).upper()
        if "-" not in ref:
            return None, 0
        prefix, num = ref.split("-", 1)
        try:
            entity_id = int(num)
        except ValueError:
            return None, 0

        mapping = {
            "PUR": "purchase", "SAL": "sale", "PRET": "purchase_return",
            "SRET": "sale_return", "PAY": "payment", "ADV": "advance"
        }
        return mapping.get(prefix), entity_id

    def open_payment_router(self):
        from apps.product_management_app.features.profiling.dialogs.payment_target_dialog import PaymentTargetDialog
        if self.walkin_name and not self.customer:
            from types import SimpleNamespace
            from shared.config.constants import WALKIN_PARTY_ID
            party = SimpleNamespace(id=WALKIN_PARTY_ID, full_name=self.walkin_name, display_name=self.walkin_name)
            dlg = PaymentTargetDialog(party=party, user_id=self.session_user_id, parent=self,
                                      walkin_name=self.walkin_name)
        else:
            dlg = PaymentTargetDialog(party=self.customer, user_id=self.session_user_id, parent=self)
        dlg.exec_()
        self.refresh_all()

    def open_advance_center(self):
        from apps.product_management_app.features.profiling.dialogs.advance_pay_dialog import AdvancePayDialog
        dlg = AdvancePayDialog(party=self.customer, user_id=self.session_user_id, parent=self)
        dlg.exec_()
        self.refresh_all()

    def _build_summary_bar(self, parent_layout):
        """Three themed groups of two figures. The value labels keep their old names."""
        row = QHBoxLayout()
        row.setSpacing(12)

        def group(heading, cards):
            box = QFrame()
            box.setObjectName("SumGroup")
            gl = QVBoxLayout(box)
            gl.setContentsMargins(14, 10, 14, 10)
            gl.setSpacing(6)
            h = QLabel(heading.upper())
            h.setObjectName("SumHeading")
            gl.addWidget(h)
            inner = QHBoxLayout()
            inner.setSpacing(18)
            labels = []
            for caption, color in cards:
                col = QVBoxLayout()
                col.setSpacing(0)
                c = QLabel(caption)
                c.setObjectName("SumCaption")
                v = QLabel("0.00")
                v.setStyleSheet(f"font-size:18px; font-weight:700; color:{color}; background:transparent;")
                col.addWidget(c)
                col.addWidget(v)
                inner.addLayout(col)
                labels.append(v)
            gl.addLayout(inner)
            row.addWidget(box, 1)
            return labels

        self.sum_payable, self.sum_paid = group("Payables", [("Payable", "#b91c1c"), ("Paid", "#c2410c")])
        self.sum_receivable, self.sum_received = group("Receivables", [("Receivable", "#047857"), ("Received", "#0f766e")])
        self.sum_adv_in, self.sum_adv_out = group("Advances", [("Advance in", "#1d4ed8"), ("Advance out", "#7c3aed")])
        parent_layout.addLayout(row)

    def _compute_totals(self):
        if not self.customer:
            if not self.walkin_name:
                return 0, 0, 0, 0, 0, 0
            from database.services.walkin_service import WalkinService
            w = WalkinService.totals(self.walkin_name)          # just this walk-in's documents
            return w["payable"], w["paid"], w["receivable"], w["received"], 0, 0

        bal = PartyBalanceService.compute(
            int(self.customer.id),
            session_user_id=self.session_user_id,
            role="customer",
        )
        overall = bal.get("overall") or {}
        return (
            float(overall.get("payable") or 0), float(overall.get("paid") or 0),
            float(overall.get("receivable") or 0), float(overall.get("received") or 0),
            float(overall.get("advance_in") or 0), float(overall.get("advance_out") or 0)
        )

    def refresh_summaries(self):
        payable, paid, receivable, received, adv_in, adv_out = self._compute_totals()
        self.sum_payable.setText(f"{payable:,.2f}")
        self.sum_paid.setText(f"{paid:,.2f}")
        self.sum_receivable.setText(f"{receivable:,.2f}")
        self.sum_received.setText(f"{received:,.2f}")
        self.sum_adv_in.setText(f"{adv_in:,.2f}")
        self.sum_adv_out.setText(f"{adv_out:,.2f}")
        self.banner_balance.setText(f"{receivable:,.2f}")

    def refresh_all(self):
        self._operator_cache = {}
        self.refresh_summaries()
        self.load_activity_table()
        self.load_crops_table()
        self.load_fertilizer_table()
        self.load_returns_table()
        self.load_payments_table()
        self.load_advances_table()
        if hasattr(self, "revert_table"):
            self.load_revert_table()
        for tab in (self.tab_docs, self.tab_products, self.tab_history):
            if tab is not None:
                tab.reload()

    def open_swap(self):
        """Swap page: starts from the rows ticked on the Sales and Purchases tabs."""
        from apps.product_management_app.features.profiling.dialogs.swap_records_dialog import SwapRecordsDialog
        dlg = SwapRecordsDialog(self, party_id=int(self.customer.id), party_name=self.customer.full_name,
                                purchase_ids=self.tab_purchases.ticked_ids(), sale_ids=self.tab_sales.ticked_ids())
        if dlg.exec_() == dlg.Accepted:
            self.refresh_all()

    def new_activity(self):
        if self.mode == "cashier":
            QMessageBox.information(self, "Access Denied", "Cashier role cannot create new activities.")
            return

        # Who is this purchase actually FROM? Pre-selects this profile's
        # own customer as a convenience default (almost always the intent
        # of clicking this button from here), but the choice stays fully
        # visible and changeable - never silently hardcoded before the
        # operator even sees a dialog. This is what previously caused
        # every purchase recorded this way to automatically, silently
        # flag this customer as a supplier too, regardless of whether
        # that was genuinely true for this specific transaction.
        from apps.product_management_app.features.cashier.dialogs.intake_start_dialog import IntakeStartDialog
        start = IntakeStartDialog(
            self,
            preselect_customer_id=int(self.customer.id) if self.customer else 0,
            preselect_customer_name=(self.customer.full_name if self.customer else ""),
        )
        if start.exec_() != start.Accepted:
            return
        source_ctx = start.context() or {}
        source_party_id = int(source_ctx.get("source_party_id") or 0)
        if source_party_id <= 0:
            QMessageBox.warning(self, "New Activity", "Select who this purchase is from.")
            return

        intake_type = str(source_ctx.get("intake_type") or "").strip().lower()
        if intake_type not in ("crops", "fertilizer"):
            QMessageBox.warning(self, "New Activity", "Select intake type (Crops or Fertilizer).")
            return

        # If the operator explicitly chose an EXISTING CUSTOMER as the
        # source, recording this purchase should not automatically,
        # silently flag them as a supplier too - that's its own,
        # deliberate decision, not a side effect of one transaction.
        # Genuine supplier sources (existing or new) still auto-flag
        # correctly, unaffected by this.
        chosen_existing_customer = (source_ctx.get("source_type") == "existing_customer")

        # Cart: same "bring in whatever products this source is
        # delivering, then finalize once" flow as the cashier's Intake
        # screen - Masar, Dawa, Wake, Waken Soya, etc. can all go into the
        # SAME purchase here too, instead of one "New Activity" round-trip
        # per product.
        source_name = str(source_ctx.get("source_name") or source_ctx.get("customer_name") or "").strip()
        display_label = source_name or (self.customer.full_name if self.customer else "Selected source")

        from apps.product_management_app.features.cashier.dialogs.intake_cart_dialog import IntakeCartDialog
        cart = IntakeCartDialog(self, intake_type=intake_type, source_label=display_label)
        if cart.exec_() != cart.Accepted:
            return

        cart_items = cart.items()
        if not cart_items:
            return

        try:
            res = state.record_intake_multi_db(
                {
                    "supplier_party_id": source_party_id,
                    "created_by_user_id": state.require_user_id(),
                    "auto_flag_supplier": not chosen_existing_customer,
                },
                cart_items,
            )

            purchase_id = int(res.get("purchase_id") or 0)
            remaining = float(res.get("remaining_payment", 0))

            # The actual source party for THIS purchase - may genuinely
            # differ from this profile's own customer if the operator
            # explicitly chose a different source in the dialog above.
            # Used consistently below rather than assuming self.customer.
            from database.session import get_session
            from database.models.party import Party as _Party
            with get_session() as _s:
                source_party = _s.get(_Party, source_party_id)
                source_party_name = source_party.display_name if source_party else ""

            # Ask whether to apply the supplier's advance (no auto deduction).
            if purchase_id > 0 and remaining > 0:
                from apps.product_management_app.features.profiling.dialogs.advance_choice_dialog import prompt_and_apply_advance
                remaining = prompt_and_apply_advance(
                    self,
                    party_id=source_party_id,
                    entity_type="purchase",
                    entity_id=purchase_id,
                    balance=remaining,
                    user_id=self.session_user_id,
                    party_name=source_party_name,
                )

            # Offer to settle their unpaid sales with these goods (never automatic).
            if purchase_id > 0 and remaining > 0:
                from apps.product_management_app.features.profiling.dialogs.offset_choice_dialog import prompt_and_apply_offset
                remaining = prompt_and_apply_offset(self, party_id=source_party_id, purchase_id=purchase_id,
                                                    party_name=source_party_name)

            if remaining > 0.005:
                from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import PurchasePaymentDialog
                pay = PurchasePaymentDialog(party=source_party, user_id=self.session_user_id, parent=self, print_slip=False)
                pay.exec_()

            self.refresh_all()                      # receipts print only when asked (Sales/Purchases tab > Print Receipt)
            
        except Exception as e:
            QMessageBox.critical(self, "Intake Failed", str(e))

    def _extract_id_from_ref(self, ref):
        m = re.search(r"(\d+)$", str(ref))
        return int(m.group(1)) if m else 0
