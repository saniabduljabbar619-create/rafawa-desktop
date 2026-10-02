# apps/product_management_app/features/orders/views/partners_page.py
"""Sales Orders = companies we sell to; Purchase Orders = suppliers we buy from. One shared pool."""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.company_service import CompanyService
from database.services.order_sheet_service import OrderSheetService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from shared.auth.session import require_user_id
from shared.uix.widgets.cards import avatar, money
from apps.product_management_app.features.debt_center.sheet_style import FAINT, MUTED, RED, STYLE

COLS = ["", "Name", "Contact person", "Phone", "Orders", "Active", "Last order", "Outstanding"]


class PartnerDialog(QDialog):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.saved_id = None
        word = "company" if kind == "sales" else "supplier"
        self.setWindowTitle(f"New {word}")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        note = QLabel("Companies and suppliers are one shared list - it will be available for Sales Orders and "
                      "Purchase Orders both.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#4a5266;")
        lay.addWidget(note)
        form = QFormLayout()
        self.name, self.contact, self.phone, self.address = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        for label, wd in (("Name", self.name), ("Contact person", self.contact), ("Phone", self.phone),
                          ("Address", self.address)):
            form.addRow(label, wd)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _save(self) -> None:
        svc = CompanyService if self.kind == "sales" else PurchaseOrderSupplierService
        try:
            self.saved_id = svc.create(self.name.text(), int(require_user_id()), address=self.address.text(),
                                       phone=self.phone.text(), contact_person=self.contact.text())
        except ValueError as e:
            QMessageBox.warning(self, "New partner", str(e))
            return
        self.accept()


class PartnersPage(QWidget):
    open_partner = Signal(int)
    created = Signal(int)

    def __init__(self, kind: str, is_admin: bool = True):
        super().__init__()
        self.kind, self.is_admin = kind, is_admin
        self._rows = []
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        head = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(0)
        t = QLabel("Companies" if kind == "sales" else "Suppliers")
        t.setObjectName("dcTitle")
        s = QLabel(("We sell to these through Sales Orders" if kind == "sales" else "We buy from these through Purchase Orders")
                   + ". Open one to see all their orders and start a new one.")
        s.setObjectName("dcSub")
        tv.addWidget(t)
        tv.addWidget(s)
        head.addLayout(tv)
        head.addStretch(1)
        self.btn_new = QPushButton("+ New " + ("company" if kind == "sales" else "supplier"))
        self.btn_new.setObjectName("msBig")
        self.btn_new.setVisible(is_admin)
        head.addWidget(self.btn_new)
        root.addLayout(head)

        self.search = QLineEdit()
        self.search.setObjectName("dcSearch")
        self.search.setPlaceholderText("Search name")
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)

        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("dcList")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setColumnWidth(0, 64)
        root.addWidget(self.table, 1)
        foot = QHBoxLayout()
        self.btn_open = QPushButton("Open profile")
        self.btn_open.setObjectName("msBig")
        foot.addWidget(self.btn_open)
        foot.addStretch(1)
        h = QLabel("Double-click a name to see their orders")
        h.setObjectName("msHint")
        foot.addWidget(h)
        root.addLayout(foot)

        self.btn_new.clicked.connect(self._new)
        self.btn_open.clicked.connect(self._open_selected)
        self.search.textChanged.connect(lambda *_: self.reload())
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_row(r))

    def refresh(self) -> None:
        self.reload()

    def reload(self) -> None:
        self._rows = OrderSheetService.partners(self.kind, self.search.text())
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            holder = QWidget()
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(12, 0, 0, 0)
            lay.addWidget(avatar(r["name"], 34))
            self.table.setCellWidget(i, 0, holder)
            vals = [r["name"], r["contact_person"], r["phone"], str(r["orders"]), str(r["active"]),
                    r["last"].strftime("%d/%m/%Y") if r["last"] else "-", money(r["outstanding"])]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 7:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor(RED if r["outstanding"] > 0.005 else (MUTED if r["orders"] else FAINT)))
                self.table.setItem(i, c, it)

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._rows):
            self.open_partner.emit(self._rows[row]["id"])

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Orders", "Select one first.")
            return
        self._open_row(row)

    def _new(self) -> None:
        dlg = PartnerDialog(self.kind, self)
        if dlg.exec_() == dlg.Accepted:
            self.reload()
            if dlg.saved_id:
                self.created.emit(dlg.saved_id)
                self.open_partner.emit(dlg.saved_id)
