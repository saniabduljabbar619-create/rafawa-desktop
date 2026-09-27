# apps/product_management_app/features/catalog/views/product_profit_view.py
from __future__ import annotations

from datetime import date
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QFrame, QPushButton, QGraphicsDropShadowEffect, QTableWidget, 
    QTableWidgetItem, QMessageBox, QDateEdit, QHeaderView, QDialog
)
from PySide2.QtCore import Qt, QDate
from PySide2.QtGui import QFont, QColor
from database.services.profit_analytics_service import ProfitAnalyticsService
from shared.uix.layout.scrollable import make_scrollable_page


class ProductProfitView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def showEvent(self, event):
        """
        🔄 Refresh statistics automatically when this analytics segment is focused.
        """
        super().showEvent(event)
        self.refresh_analytics()

    def _init_ui(self):
        main_layout = make_scrollable_page(self, margins=(30, 30, 30, 30), spacing=20)
        self.setStyleSheet("background-color: #f8f9fa;")

        # ---------------------------------------------------------
        # HEADER & TOOLBAR CONTROLS
        # ---------------------------------------------------------
        header_layout = QHBoxLayout()
        title_label = QLabel("Realized Profit & Margin Analytics")
        title_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title_label.setStyleSheet("color: #212529;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        # 🗓️ DATE FILTER INTERFACE CONTROLS
        header_layout.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setMinimumHeight(34)
        self.date_from.setDate(QDate.currentDate())
        self.date_from.setStyleSheet("QDateEdit { background: white; border: 1px solid #ced4da; padding-left: 5px; border-radius: 4px; }")
        header_layout.addWidget(self.date_from)

        header_layout.addWidget(QLabel("To:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setMinimumHeight(34)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setStyleSheet("QDateEdit { background: white; border: 1px solid #ced4da; padding-left: 5px; border-radius: 4px; }")
        header_layout.addWidget(self.date_to)
        
        refresh_btn = QPushButton("Refresh Metrics")
        refresh_btn.setFont(QFont("Segoe UI", 10, QFont.Bold))
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setMinimumHeight(36)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4; color: white; border: none;
                padding-left: 15px; padding-right: 15px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #005a9e; }
            QPushButton:pressed { background-color: #004578; }
        """)
        refresh_btn.clicked.connect(self.refresh_analytics)
        header_layout.addWidget(refresh_btn)
        
        main_layout.addLayout(header_layout)

        # ---------------------------------------------------------
        # METRIC OVERVIEW TILES GRID (🚀 UPGRADED FOR LOSS LOGIC)
        # ---------------------------------------------------------
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_rev = self._create_metric_tile("Net Sales Revenue", "#0078d4")
        self.card_cogs = self._create_metric_tile("Cost Basis (COGS)", "#ca5010")
        self.card_profit = self._create_metric_tile("Realized Gains", "#107c41")
        self.card_loss = self._create_metric_tile("Realized Losses", "#d13438") # Dedicated Price Drop Tracker Card
        self.card_margin = self._create_metric_tile("Net Margin %", "#8e44ad")

        cards_layout.addWidget(self.card_rev)
        cards_layout.addWidget(self.card_cogs)
        cards_layout.addWidget(self.card_profit)
        cards_layout.addWidget(self.card_loss)
        cards_layout.addWidget(self.card_margin)
        main_layout.addLayout(cards_layout)

        # ---------------------------------------------------------
        # PRODUCT DETAIL TABLE GRID
        # ---------------------------------------------------------
        table_label = QLabel("Product Profit & Loss Breakdowns")
        table_label.setFont(QFont("Segoe UI", 12, QFont.Bold))
        table_label.setStyleSheet("color: #495057; margin-top: 10px;")
        main_layout.addWidget(table_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Product Name", "Category", "Volume Sold", "Net Revenue", "Cost Basis (COGS)", "Net Margin Profit/Loss"
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget { background-color: white; border-radius: 8px; border: 1px solid #dee2e6; }
            QHeaderView::section { background-color: #f1f3f5; font-weight: bold; border: none; height: 36px; }
        """)
        main_layout.addWidget(self.table, 1)

        # Double-click a product row to see the individual sales behind it.
        self.table.cellDoubleClicked.connect(self._open_product_detail)
        # Sync table headers smoothly
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

        # Live update if dates change interactively
        self.date_from.dateChanged.connect(self.refresh_analytics)
        self.date_to.dateChanged.connect(self.refresh_analytics)

    def _create_metric_tile(self, title: str, accent_color: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("MetricCardWidget")
        tile.setMinimumHeight(130)
        tile.setStyleSheet(f"""
            QFrame#MetricCardWidget {{
                background-color: white; border-radius: 8px;
                border-left: 5px solid {accent_color};
            }}
        """)
        
        shadow = QGraphicsDropShadowEffect(tile)
        shadow.setBlurRadius(12)
        shadow.setXOffset(0)
        shadow.setYOffset(3)
        shadow.setColor(QColor(0, 0, 0, 10))
        tile.setGraphicsEffect(shadow)

        layout = QVBoxLayout(tile)
        layout.setContentsMargins(14, 14, 14, 14)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #6c757d; text-transform: uppercase;")
        layout.addWidget(title_lbl, 0, Qt.AlignTop)
        layout.addStretch()

        value_lbl = QLabel("₦0.00")
        value_lbl.setStyleSheet(f"color: {accent_color}; font-size: 24px; font-weight: bold;")
        layout.addWidget(value_lbl, 0, Qt.AlignBottom)
        
        tile.setProperty("ui_display_lbl", value_lbl)
        return tile
    
    
    

    def refresh_analytics(self):
        """
        🚀 Reads date coordinates from UI pickers and repopulates the analytics ledger.
        """
        try:
            q_from = self.date_from.date()
            q_to = self.date_to.date()
            
            start_date_obj = date(q_from.year(), q_from.month(), q_from.day())
            end_date_obj = date(q_to.year(), q_to.month(), q_to.day())
            
            if start_date_obj > end_date_obj:
                return  

            # Remember the active range so the per-product drill-down matches it.
            self._start_obj = start_date_obj
            self._end_obj = end_date_obj 

            # Fetch calculation payload dictionary directly from DB service layer
            data = ProfitAnalyticsService.get_profit_loss_summary_db(start_date=start_date_obj, end_date=end_date_obj)
            
            summary = data.get("summary", {})
            items = data.get("items", [])

            # Update core metrics summary text properties
            self.card_rev.property("ui_display_lbl").setText(f"₦{summary.get('gross_revenue', 0.0):,.2f}")
            self.card_cogs.property("ui_display_lbl").setText(f"₦{summary.get('total_cogs', 0.0):,.2f}")
            
            # Extract independent positive gains vs negative price drop parameters cleanly
            gains = summary.get('total_gains', 0.0)
            losses = abs(summary.get('total_losses', 0.0))  # Keep UI clean and absolute
            
            self.card_profit.property("ui_display_lbl").setText(f"₦{gains:,.2f}")
            self.card_loss.property("ui_display_lbl").setText(f"₦{losses:,.2f}")
            self.card_margin.property("ui_display_lbl").setText(f"{summary.get('margin_pct', 0.0):,.1f}%")

            # Hydrate product table grid lines
            self.table.setRowCount(0)
            for item in items:
                row = self.table.rowCount()
                self.table.insertRow(row)

                p_profit = item["profit"]
                # Dynamic text color coding: Green for profit gains, Red for true operational losses
                profit_color = "#107c41" if p_profit >= 0 else "#d13438"
                prefix = "₦" if p_profit >= 0 else "-₦"

                cols = [
                    str(item["product_name"]),
                    str(item["category"]),
                    str(item["volume_sold"]),
                    f"₦{item['revenue']:,.2f}",
                    f"₦{item['cogs']:,.2f}",
                    f"{prefix}{abs(p_profit):,.2f}"
                ]

                for c, val in enumerate(cols):
                    table_item = QTableWidgetItem(val)
                    if c == 0:
                        table_item.setData(Qt.UserRole, int(item.get("product_id") or 0))
                    if c >= 2:
                        table_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if c == 5:
                        table_item.setForeground(QColor(profit_color))
                        f = QFont()
                        f.setBold(True)
                        table_item.setFont(f)
                    self.table.setItem(row, c, table_item)

        except Exception as e:
            QMessageBox.critical(self, "Analytics Sync Error", f"Failed to compute timeline metrics:\n{str(e)}")
            
            
            
    def _open_product_detail(self, row, col):
        """Drill into the individual sales behind a product's profit row."""
        name_item = self.table.item(row, 0)
        if not name_item:
            return
        product_id = int(name_item.data(Qt.UserRole) or 0)
        if product_id <= 0:
            return
        product_name = name_item.text()
        start = getattr(self, "_start_obj", None)
        end = getattr(self, "_end_obj", None)
        try:
            lines = ProfitAnalyticsService.get_product_sale_lines(product_id, start, end)
        except Exception as e:
            QMessageBox.critical(self, "Details", str(e))
            return
        _ProductProfitDetailDialog(product_name, lines, self).exec_()


class _ProductProfitDetailDialog(QDialog):
    def __init__(self, product_name, lines, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Sales detail - {product_name}")
        self.setMinimumSize(920, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel(f"{product_name}  -  {len(lines)} sale line(s)")
        hdr.setStyleSheet("font-size:14px; font-weight:800;")
        lay.addWidget(hdr)

        cols = ["Sale", "Date", "Customer", "Sold by", "Qty",
                "Rate", "Unit Cost", "Revenue", "COGS", "Profit"]
        tbl = QTableWidget(len(lines), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(True)

        def _dt(d):
            try:
                return d.strftime("%Y-%m-%d %H:%M")
            except Exception:
                return str(d or "")

        for r, ln in enumerate(lines):
            profit = float(ln["profit"])
            naira = "\u20a6"
            prof_prefix = naira if profit >= 0 else "-" + naira
            vals = [
                ln["sale_no"], _dt(ln["date"]), ln["customer"], ln["operator"],
                f'{ln["qty"]:,.2f} {ln["unit"]}',
                f'{naira}{ln["rate"]:,.2f}', f'{naira}{ln["cost_rate"]:,.2f}',
                f'{naira}{ln["revenue"]:,.2f}', f'{naira}{ln["cogs"]:,.2f}',
                f'{prof_prefix}{abs(profit):,.2f}',
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 9:
                    it.setForeground(QColor("#107c41" if profit >= 0 else "#d13438"))
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)
                tbl.setItem(r, c, it)
        lay.addWidget(tbl, 1)

        tot_rev = sum(float(l["revenue"]) for l in lines)
        tot_cogs = sum(float(l["cogs"]) for l in lines)
        tot_profit = sum(float(l["profit"]) for l in lines)
        foot = QLabel(
            f"Totals   -   Revenue \u20a6{tot_rev:,.2f}    "
            f"COGS \u20a6{tot_cogs:,.2f}    Profit \u20a6{tot_profit:,.2f}"
        )
        foot.setStyleSheet("font-weight:700;")
        lay.addWidget(foot)

        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        lay.addLayout(row)