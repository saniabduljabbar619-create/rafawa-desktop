from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QHBoxLayout
)

from database.session import get_session
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.purchase_return import PurchaseReturn
from database.models.product import Product
from database.models.user import User


def _fmt_dt(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)


class PurchaseHistoryDialog(QDialog):
    def __init__(self, purchase_id: int, parent=None):
        super().__init__(parent)
        self.purchase_id = int(purchase_id)

        self.setWindowTitle(f"Purchase History • #{self.purchase_id}")
        self.setMinimumSize(860, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.header = QLabel("")
        self.header.setStyleSheet("font-size: 14px; font-weight: 800;")
        root.addWidget(self.header)

        self.meta = QLabel("")
        self.meta.setStyleSheet("color: #666;")
        self.meta.setWordWrap(True)
        root.addWidget(self.meta)

        items_lbl = QLabel("Items")
        items_lbl.setStyleSheet("font-weight: 700;")
        root.addWidget(items_lbl)

        self.items_tbl = QTableWidget(0, 5)
        self.items_tbl.setHorizontalHeaderLabels(["Product", "Type", "Bags", "KG", "Unit Cost"])
        self.items_tbl.verticalHeader().setVisible(False)
        self.items_tbl.setEditTriggers(self.items_tbl.NoEditTriggers)
        self.items_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.items_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        root.addWidget(self.items_tbl, 1)

        returns_lbl = QLabel("Purchase Returns (refund → goods out)")
        returns_lbl.setStyleSheet("font-weight: 700;")
        root.addWidget(returns_lbl)

        self.returns_tbl = QTableWidget(0, 7)
        self.returns_tbl.setHorizontalHeaderLabels([
            "Return ID", "Product", "Bags", "KG", "Reason", "Created At", "Created By"
        ])
        self.returns_tbl.verticalHeader().setVisible(False)
        self.returns_tbl.setEditTriggers(self.returns_tbl.NoEditTriggers)
        self.returns_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.returns_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.returns_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        root.addWidget(self.returns_tbl, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._load()

    def _load(self):
        with get_session() as s:
            p = s.query(Purchase).filter(Purchase.id == self.purchase_id).first()
            if not p:
                self.header.setText("Purchase not found.")
                return

            u = s.query(User).filter(User.id == p.created_by_user_id).first()
            uname = getattr(u, "full_name", None) or getattr(u, "username", None) or f"User#{p.created_by_user_id}"

            self.header.setText(f"Purchase #{p.id} • {p.purchase_no or ''}".strip())

            meta = (
                f"Status: {p.status} | Total: {float(p.total_amount or 0):.2f}\n"
                f"Created: {_fmt_dt(p.created_at)} by {uname}\n"
                f"Completed: {_fmt_dt(getattr(p, 'completed_at', None))}"
            )
            self.meta.setText(meta)

            items = (
                s.query(PurchaseItem)
                .filter(PurchaseItem.purchase_id == self.purchase_id)
                .order_by(PurchaseItem.id.asc())
                .all()
            )

            pids = {int(it.product_id) for it in items}
            name_by_pid = {
                int(pid): str(pname)
                for pid, pname in (
                    s.query(Product.id, Product.name).filter(Product.id.in_(pids)).all()
                    if pids else []
                )
            }
            pid_by_item = {int(it.id): int(it.product_id) for it in items}

            def _pname_for_item(item_id):
                pid = pid_by_item.get(int(item_id))
                return name_by_pid.get(pid, f"Product #{pid}" if pid else "-")

            self.items_tbl.setRowCount(len(items))
            for r, it in enumerate(items):
                pname = name_by_pid.get(int(it.product_id), f"Product #{it.product_id}")
                self.items_tbl.setItem(r, 0, QTableWidgetItem(pname))
                self.items_tbl.setItem(r, 1, QTableWidgetItem(str(it.product_type_snapshot)))
                self.items_tbl.setItem(r, 2, QTableWidgetItem(str(int(it.qty_bags or 0))))
                self.items_tbl.setItem(r, 3, QTableWidgetItem(f"{float(it.total_weight_kg or 0):.2f}"))
                self.items_tbl.setItem(r, 4, QTableWidgetItem(f"{float(it.unit_cost_rate or 0):.2f}"))

            rets = (
                s.query(PurchaseReturn)
                .filter(PurchaseReturn.purchase_id == self.purchase_id)
                .order_by(PurchaseReturn.id.desc())
                .all()
            )

            user_ids = list({int(r.created_by_user_id) for r in rets})
            user_map = {}
            if user_ids:
                for uu in s.query(User).filter(User.id.in_(user_ids)).all():
                    nm = getattr(uu, "full_name", None) or getattr(uu, "username", None) or f"User#{uu.id}"
                    user_map[int(uu.id)] = nm

            self.returns_tbl.setRowCount(len(rets))
            for r, ret in enumerate(rets):
                self.returns_tbl.setItem(r, 0, QTableWidgetItem(str(ret.id)))
                self.returns_tbl.setItem(r, 1, QTableWidgetItem(_pname_for_item(ret.purchase_item_id)))
                self.returns_tbl.setItem(r, 2, QTableWidgetItem(str(int(ret.qty_bags or 0))))
                self.returns_tbl.setItem(r, 3, QTableWidgetItem(f"{float(ret.qty_kg or 0):.2f}"))
                self.returns_tbl.setItem(r, 4, QTableWidgetItem(str(ret.reason or "")))
                self.returns_tbl.setItem(r, 5, QTableWidgetItem(_fmt_dt(ret.created_at)))
                self.returns_tbl.setItem(
                    r, 6,
                    QTableWidgetItem(user_map.get(int(ret.created_by_user_id), f"User#{ret.created_by_user_id}"))
                )