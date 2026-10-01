# apps/product_management_app/features/reports/views/reports_view.py
"""Management reports: period summary, receivables/payables aging, stock valuation, Excel export."""
from __future__ import annotations

from datetime import date

from PySide2.QtCore import QDate, Qt
from PySide2.QtWidgets import (QDateEdit, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from database.services.report_export import export_reports
from database.services.report_service import AGE_BUCKETS, ReportService


def _money(v) -> str:
    return f"{float(v or 0):,.2f}"


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setAlternatingRowColors(True)
    return t


def _fill(table: QTableWidget, rows, money_cols=(), bold_last=False):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem(_money(val) if c in money_cols else str(val))
            if c in money_cols:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if bold_last and r == len(rows) - 1:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            table.setItem(r, c, item)


class ReportsView(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        bar = QHBoxLayout()
        title = QLabel("Reports")
        title.setStyleSheet("font-size:18px; font-weight:700;")
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(QLabel("From"))
        self.d_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.d_to = QDateEdit(QDate.currentDate())
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(self.d_from)
        bar.addWidget(QLabel("to"))
        bar.addWidget(self.d_to)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_export = QPushButton("Export to Excel")
        self.btn_export.setObjectName("PrimaryButton")
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

        self.tabs = QTabWidget()
        self.summary = _table(["Item", "Amount"])
        self.cashiers = _table(["Cashier", "Sales", "Purchases", "Cash in", "Cash out"])
        self.methods = _table(["Payment method", "In", "Out", "Net"])
        age_cols = ["Party", "Documents"] + [f"{b[0]} days" for b in AGE_BUCKETS] + ["Total owed"]
        self.receivables = _table(age_cols)
        self.payables = _table(age_cols)
        self.stock = _table(["Product", "SKU", "Bags", "Kg", "Unit cost", "Value"])
        for widget, label in ((self.summary, "Summary"), (self.cashiers, "By cashier"),
                              (self.methods, "By payment method"), (self.receivables, "Receivables"),
                              (self.payables, "Payables"), (self.stock, "Stock valuation")):
            self.tabs.addTab(widget, label)
        root.addWidget(self.tabs, 1)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_export.clicked.connect(self._export)
        self.refresh()

    def _range(self):
        return self.d_from.date().toPython(), self.d_to.date().toPython()

    def refresh(self):
        start, end = self._range()
        rep = ReportService.period_summary(start, end)
        sa, pu, ca, ad = rep["sales"], rep["purchases"], rep["cash"], rep["advances"]
        _fill(self.summary, [
            ("Sales (documents)", sa["count"]), ("Sales - gross", sa["gross"]),
            ("Sales - returns", -sa["returns"]), ("Sales - manual discounts", -sa["manual_discounts"]),
            ("Sales - NET", sa["net"]), ("Purchases (documents)", pu["count"]),
            ("Purchases - gross", pu["gross"]), ("Purchases - returns", -pu["returns"]),
            ("Purchases - NET", pu["net"]), ("Cash received", ca["in"]), ("Cash paid out", -ca["out"]),
            ("Net cash movement", ca["net"]), ("Advances received", ad["received"]),
            ("Advances given", -ad["given"]),
        ], money_cols=(1,))
        # document counts are not money
        for r in (0, 5):
            self.summary.item(r, 1).setText(str(int(self.summary.item(r, 1).text().replace(",", "").split(".")[0])))
        _fill(self.cashiers, [(c["username"], c["sales"], c["purchases"], c["cash_in"], c["cash_out"])
                              for c in rep["by_cashier"]], money_cols=(1, 2, 3, 4))
        _fill(self.methods, [(m["method"], m["in"], m["out"], m["net"]) for m in rep["by_method"]],
              money_cols=(1, 2, 3))
        for table, kind in ((self.receivables, "sale"), (self.payables, "purchase")):
            rows = ReportService.aging(kind, date.today())
            body = [(r["party"], r["documents"], *[r[b[0]] for b in AGE_BUCKETS], r["total"]) for r in rows]
            if body:
                totals = [sum(r[i] for r in body) for i in range(1, len(body[0]))]
                body.append(("TOTAL", *totals))
            _fill(table, body, money_cols=tuple(range(2, 2 + len(AGE_BUCKETS) + 1)), bold_last=bool(body))
        stock = ReportService.stock_valuation()
        body = [(r["product"], r["sku"], f"{r['bags']:,.0f}", f"{r['kg']:,.2f}", r["unit_cost"], r["value"])
                for r in stock]
        if body:
            body.append(("TOTAL", "", "", "", "", sum(r["value"] for r in stock)))
        _fill(self.stock, body, money_cols=(4, 5), bold_last=bool(body))

    def _export(self):
        start, end = self._range()
        path, _ = QFileDialog.getSaveFileName(self, "Export reports", f"Rafawa_Reports_{date.today()}.xlsx",
                                              "Excel Files (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_reports(path, start, end, date.today())
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        QMessageBox.information(self, "Export complete", f"Reports saved to:\n{path}")
