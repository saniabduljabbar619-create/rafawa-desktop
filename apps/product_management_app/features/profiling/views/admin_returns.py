# apps/product_management_app/features/profiling/views/admin_returns.py

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QSpinBox, QTextEdit, QPushButton, QMessageBox,
    QFormLayout, QGroupBox, QTableWidget, QTableWidgetItem, QCheckBox,
    QHeaderView, QFrame
)

from sqlalchemy import func

from database.session import get_session
from database.models.party import Party

from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.sale_return import SaleReturn
from database.services.return_service import ReturnService

from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.purchase_return import PurchaseReturn
from apps.product_management_app.app_state import state
from shared.uix.layout.scrollable import make_scrollable_dialog
from apps.product_management_app.features.receipts_v2 import (
    print_full_then_optional_delivery, SALE_RETURN, PURCHASE_RETURN,
)

RETURN_TYPE_SALES = "sales"
RETURN_TYPE_PURCHASE = "purchase"
RETURN_TYPE_INTAKE = "intake"  # future


class AdminReturnsView(QWidget):
    def __init__(self, mode=None, preselect_party_id=None):
        super().__init__()
        self.mode = (mode or "admin").strip().lower()
        self.session_user_id = int(state.require_user_id())
        # Set when opened from the Walk-ins screen's right-click "Go to
        # Returns" - that specific walk-in gets added to the dropdown and
        # pre-selected, even though walk-ins are otherwise excluded from
        # this screen's general list (see _load_customers below).
        self._preselect_party_id = int(preselect_party_id) if preselect_party_id else None

        root, action_footer = make_scrollable_dialog(self, margins=(16, 16, 16, 16), spacing=12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Returns")
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        subtitle = QLabel(
            "Admin Only • Additive returns with full audit trail"
            if self.mode == "admin"
            else "Cashier • Returns (only your activity)"
        )

        subtitle.setStyleSheet("color: #666;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(subtitle)
        root.addLayout(header)

        # ================= SETUP ROW (horizontal, compact) =================
        setup_card = self._card()
        setup_row = QHBoxLayout(setup_card)
        setup_row.setContentsMargins(14, 12, 14, 12)
        setup_row.setSpacing(16)

        setup_row.addWidget(QLabel("Party / Customer:"))
        self.customer_cb = QComboBox()

        # 🚀 SMART SEARCH ENGINE ACTIVATED
        self.customer_cb.setEditable(True)  # Allows typing inside the dropdown input field
        self.customer_cb.setInsertPolicy(QComboBox.NoInsert)  # Prevents typed text from saving as a fake item

        # Configure the default auto-completer to filter by substring matches instead of matching from the start of the word
        if self.customer_cb.completer():
            self.customer_cb.completer().setFilterMode(Qt.MatchContains)
            self.customer_cb.completer().setCompletionMode(self.customer_cb.completer().PopupCompletion)

        # Optional placeholder look when empty
        if self.customer_cb.lineEdit():
            self.customer_cb.lineEdit().setPlaceholderText("Search name or phone parameters here...")
        setup_row.addWidget(self.customer_cb, 2)

        setup_row.addWidget(QLabel("Return Category:"))
        self.return_type_cb = QComboBox()
        self.return_type_cb.addItem("Sales Return (customer brings goods back)", RETURN_TYPE_SALES)
        self.return_type_cb.addItem("Purchase Return (refund → goods out of store)", RETURN_TYPE_PURCHASE)
        setup_row.addWidget(self.return_type_cb, 2)

        root.addWidget(setup_card)

        # ================= COMPLETED SALES/PURCHASES - full width =================
        self.tx_box = QGroupBox("Completed Sales")
        self.tx_box.setStyleSheet("QGroupBox { font-weight: 700; }")
        tx_layout = QVBoxLayout(self.tx_box)

        self.sales_table = QTableWidget(0, 7)
        self.sales_table.setHorizontalHeaderLabels([
            "Select", "Sale ID", "Sale No", "Completed At", "Total", "Status", "Products"
        ])
        self.sales_table.verticalHeader().setVisible(False)
        self.sales_table.setAlternatingRowColors(True)
        self.sales_table.setSelectionBehavior(self.sales_table.SelectRows)
        self.sales_table.setEditTriggers(self.sales_table.NoEditTriggers)
        self.sales_table.setMinimumHeight(160)

        tx_layout.addWidget(self.sales_table)
        root.addWidget(self.tx_box)

        self.sales_table.cellDoubleClicked.connect(self._open_tx_preview)
        self._polish_table(self.sales_table, is_items=False)

        # ================= ITEMS TO RETURN - full width, the main working area =================
        items_card = self._card()
        items_layout = QVBoxLayout(items_card)
        items_layout.setContentsMargins(14, 14, 14, 14)
        items_layout.setSpacing(8)

        self.details_title = QLabel("Items to Return")
        self.details_title.setStyleSheet("font-size: 14px; font-weight: 800;")
        items_layout.addWidget(self.details_title)

        self.hint = QLabel(
            "How to make a return:\n"
            "1.  Pick the customer and the return type above.\n"
            "2.  Tick the completed sale(s) the goods came from.\n"
            "3.  For each product below, type how many bags/kg are coming back\n"
            "     (you can't exceed the Available amount).\n"
            "4.  Add a reason if you like, then click Create Return(s)."
        )
        self.hint.setStyleSheet("color: #555;")
        items_layout.addWidget(self.hint)

        self.items_table = QTableWidget(0, 12)
        self.items_table.setHorizontalHeaderLabels([
            "Return?", "Product", "Type", "From Sale",
            "Sold (bags)", "Sold (kg)", "Returned (bags)", "Returned (kg)",
            "Available (bags)", "Available (kg)", "Return (bags)", "Return (kg)"
        ])
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setSelectionBehavior(self.items_table.SelectRows)
        self.items_table.setEditTriggers(self.items_table.NoEditTriggers)
        self.items_table.setMinimumHeight(320)

        items_layout.addWidget(self.items_table, 1)
        self._polish_table(self.items_table, is_items=True)

        self.footer = QLabel("")
        self.footer.setStyleSheet("color: #666;")
        items_layout.addWidget(self.footer)

        root.addWidget(items_card, 1)

        # ================= REASON + ACTIONS - pinned footer, always visible =================
        reason_box = QGroupBox("Reason / Notes")
        reason_box.setStyleSheet("QGroupBox { font-weight: 700; }")
        reason_layout = QVBoxLayout(reason_box)
        self.reason = QTextEdit()
        self.reason.setPlaceholderText("Optional: why is this return being performed?")
        self.reason.setFixedHeight(70)
        reason_layout.addWidget(self.reason)
        action_footer.addWidget(reason_box, 1)

        btn_col = QVBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setMinimumHeight(36)
        self.submit_btn = QPushButton("Create Return(s)")
        self.submit_btn.setMinimumHeight(36)
        self.submit_btn.setStyleSheet("font-weight: 700;")
        self.print_btn = QPushButton("Print Receipt(s)")
        self.print_btn.setMinimumHeight(36)
        self.print_btn.setEnabled(False)
        btn_col.addWidget(self.refresh_btn)
        btn_col.addWidget(self.print_btn)
        btn_col.addWidget(self.submit_btn)
        action_footer.addLayout(btn_col)

        # caches
        self._customers = []
        self._sale_no_by_id = {}
        self._purchase_no_by_id = {}
        # No longer auto-printed - tracks the most recently created batch so
        # Print Receipt(s) can print all of them on demand, once.
        self._last_return_ids: list = []
        self._last_return_doc_type: str = ""

        # signals
        self.customer_cb.currentIndexChanged.connect(self._reload_for_selection)
        self.return_type_cb.currentIndexChanged.connect(self._reload_for_selection)

        self.refresh_btn.clicked.connect(self._reload_for_selection)
        self.submit_btn.clicked.connect(self._create_returns)
        self.print_btn.clicked.connect(self._print_last_returns)

        # init
        self._load_customers()

    # ---------------- UI helpers ----------------

    def _is_fertilizer_text(self, ptype: str) -> bool:
        s = (ptype or "").strip().lower()
        return s in ("fertilizer", "fertilizers")

    def _card(self) -> QFrame:
        w = QFrame()
        w.setObjectName("Card")
        w.setFrameShape(QFrame.StyledPanel)
        w.setStyleSheet("""
            QFrame#Card {
                background: #ffffff;
                border: 1px solid #e6e6e6;
                border-radius: 12px;
            }
        """)
        return w

    def _clear_tables(self):
        self.sales_table.setRowCount(0)
        self.items_table.setRowCount(0)

    def _product_summary_for(self, s, ItemModel, id_col, tx_ids):
        """Map each tx id -> short product summary, e.g. 'Masara, NPK (+1 more)'."""
        from database.models.product import Product
        from collections import defaultdict
        out = {}
        if not tx_ids:
            return out
        rows = (
            s.query(id_col, Product.name)
            .join(Product, Product.id == ItemModel.product_id)
            .filter(id_col.in_(tx_ids))
            .order_by(id_col.asc(), ItemModel.id.asc())
            .all()
        )
        tmp = defaultdict(list)
        for tx_id, pname in rows:
            tmp[int(tx_id)].append(str(pname))
        for tx_id, names in tmp.items():
            uniq = list(dict.fromkeys(names))
            if len(uniq) <= 2:
                out[tx_id] = ", ".join(uniq)
            else:
                out[tx_id] = ", ".join(uniq[:2]) + f" (+{len(uniq) - 2} more)"
        return out

    #------------------- 2nd helpers ------------------

    def _polish_table(self, t: QTableWidget, *, is_items: bool) -> None:
        t.setWordWrap(False)
        t.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        t.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
        t.setShowGrid(True)

        hh = t.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        hh.setMinimumHeight(34)
        hh.setStretchLastSection(True)
        hh.setHighlightSections(False)

        t.setStyleSheet("""
            QHeaderView::section {
                font-weight: 800;
                padding: 6px 10px;
                border: none;
                border-bottom: 1px solid #e6e6e6;
                background: #f7f7f7;
            }
            QTableWidget {
                gridline-color: #ededed;
            }
        """)

        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setMinimumSectionSize(50)

        if not is_items:
            widths = {0: 70, 1: 80, 2: 120, 3: 150, 4: 120, 5: 90, 6: 220}
        else:
            widths = {0: 70, 1: 80, 2: 120, 3: 80, 4: 90, 5: 110, 6: 95, 7: 95, 8: 115, 9: 115, 10: 105, 11: 105}

        for col, w in widths.items():
            t.setColumnWidth(col, w)

        def _right(col: int):
            for r in range(t.rowCount()):
                it = t.item(r, col)
                if it:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        if is_items:
            for c in (1, 3, 4, 6, 7, 8, 9):
                _right(c)
        else:
            _right(4)

    def _repolish_tables(self) -> None:
        self._polish_table(self.sales_table, is_items=False)
        self._polish_table(self.items_table, is_items=True)

    # ---------------- Loading ----------------

    def _load_customers(self):
        with get_session() as s:
            rows = (
                s.query(Party)
                .filter(Party.is_active == True)  # noqa: E712
                .filter(Party.party_type != "walkin")
                .filter(
                    (Party.is_customer == True)   # noqa: E712
                    | (Party.is_supplier == True)  # noqa: E712
                )
                .order_by(Party.display_name.asc())
                .all()
            )

            # Walk-ins are deliberately excluded from this general list - they
            # live in their own dedicated screen now, reached only through
            # its right-click "Go to Returns" action. That action passes the
            # specific walk-in through here via preselect_party_id, so THAT
            # one gets added and selected even though walk-ins otherwise
            # never populate this dropdown.
            if self._preselect_party_id:
                already_present = any(c.id == self._preselect_party_id for c in rows)
                if not already_present:
                    p = s.get(Party, self._preselect_party_id)
                    if p is not None:
                        rows = list(rows) + [p]

        self._customers = rows

        # Block signals temporarily to prevent infinite refresh cycles while rebuilding lists
        self.customer_cb.blockSignals(True)
        self.customer_cb.clear()

        preselect_index = None
        for i, c in enumerate(rows):
            lbl = "[Walk-in] " if str(c.party_type).strip().lower() == "walkin" else "[Regular] "
            ident = getattr(c, "walkin_code", None) or f"#_ID:{c.id}"
            self.customer_cb.addItem(f"{lbl}{c.display_name} ({ident})", c.id)
            if self._preselect_party_id and c.id == self._preselect_party_id:
                preselect_index = i

        if preselect_index is not None:
            self.customer_cb.setCurrentIndex(preselect_index)
        elif rows:
            self.customer_cb.setCurrentIndex(0)

        self.customer_cb.blockSignals(False)
        self._reload_for_selection()

    def _reload_for_selection(self):
        rtype = self.return_type_cb.currentData()
        self._clear_tables()

        if rtype == RETURN_TYPE_SALES:
            self.tx_box.setTitle("Completed Sales")
            self.sales_table.setHorizontalHeaderLabels(["Select", "Sale ID", "Sale No", "Completed At", "Total", "Status", "Products"])
            self.details_title.setText("Sale Items (Aggregated)")
            self.hint.setText("Tick one or more completed sales to load items.")
            self._load_sales_for_customer()
            return

        if rtype == RETURN_TYPE_PURCHASE:
            self.tx_box.setTitle("Completed Purchases")
            self.sales_table.setHorizontalHeaderLabels(["Select", "Purchase ID", "Purchase No", "Completed At", "Total", "Status", "Products"])
            self.details_title.setText("Purchase Items (Aggregated)")
            self.hint.setText("Tick one or more completed purchases to load items (refund → goods out).")
            self._load_purchases_for_customer()
            return

        self.footer.setText("This return category is not implemented yet.")
        self.hint.setText("This return category is not implemented yet.")
        self._repolish_tables()

    # ---------------- SALES path ----------------

    def _load_sales_for_customer(self):
        self._sale_no_by_id = {}
        cust_id = self.customer_cb.currentData()
        if not cust_id:
            self.footer.setText("No selection target resolved.")
            return

        with get_session() as s:
            q = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == int(cust_id))
                .filter(Sale.status == "completed")
            )
            if self.mode != "admin":
                q = q.filter(Sale.created_by_user_id == self.session_user_id)

            rows = q.order_by(Sale.completed_at.desc(), Sale.id.desc()).all()
            prod_summary = self._product_summary_for(
                s, SaleItem, SaleItem.sale_id, [int(x.id) for x in rows]
            )
            

        self.sales_table.setRowCount(len(rows))
        for r, sale in enumerate(rows):
            sid = int(sale.id)
            self._sale_no_by_id[sid] = str(sale.sale_no or "")

            chk = QCheckBox()
            chk.setChecked(False)
            chk.setProperty("tx_id", sid)
            chk.stateChanged.connect(self._load_items_for_selected_sales)
            self.sales_table.setCellWidget(r, 0, chk)

            self.sales_table.setItem(r, 1, QTableWidgetItem(str(sid)))
            self.sales_table.setItem(r, 2, QTableWidgetItem(str(sale.sale_no or "")))

            completed_at = getattr(sale, "completed_at", None)
            completed_txt = completed_at.strftime("%Y-%m-%d %H:%M") if completed_at else ""
            self.sales_table.setItem(r, 3, QTableWidgetItem(completed_txt))

            total = float(getattr(sale, "total_amount", 0) or 0)
            self.sales_table.setItem(r, 4, QTableWidgetItem(f"{total:.2f}"))
            self.sales_table.setItem(r, 5, QTableWidgetItem(str(sale.status)))
            self.sales_table.setItem(r, 6, QTableWidgetItem(prod_summary.get(sid, "")))

        if rows:
            self.footer.setText(f"Loaded {len(rows)} completed transaction file(s).")
        else:
            self.footer.setText("No completed receipts resolved for this selector record.")

    def _selected_tx_ids(self):
        ids = []
        for row in range(self.sales_table.rowCount()):
            chk = self.sales_table.cellWidget(row, 0)
            if chk and chk.isChecked():
                ids.append(int(chk.property("tx_id")))
        return ids

    def _load_items_for_selected_sales(self):
        if self.return_type_cb.currentData() != RETURN_TYPE_SALES:
            return

        sale_ids = self._selected_tx_ids()
        self.items_table.setRowCount(0)
        if not sale_ids:
            self.footer.setText("No source logs selected. Tick rows to load sub-items.")
            return

        with get_session() as s:
            items = (
                s.query(SaleItem)
                .filter(SaleItem.sale_id.in_(sale_ids))
                .order_by(SaleItem.sale_id.asc(), SaleItem.id.asc())
                .all()
            )

            rows = (
                s.query(
                    SaleReturn.sale_item_id,
                    func.coalesce(func.sum(SaleReturn.qty_bags), 0),
                    func.coalesce(func.sum(SaleReturn.qty_kg), 0.0),
                )
                .filter(SaleReturn.sale_id.in_(sale_ids))
                .group_by(SaleReturn.sale_item_id)
                .all()
            )

        # 🚀 FIXED: Explicit tuple unpacking strips individual scalar items safely from the database Row wrappers
        sums = {int(item_id): (int(bags or 0), float(kg or 0.0)) for item_id, bags, kg in rows}

        from database.models.product import Product
        product_ids = {int(getattr(it, "product_id")) for it in items}
        with get_session() as s:
            name_rows = (
                s.query(Product.id, Product.name).filter(Product.id.in_(product_ids)).all()
                if product_ids else []
            )
        names = {int(pid): str(pname) for pid, pname in name_rows}

        self.items_table.setHorizontalHeaderLabels([
            "Return?", "Product", "Type", "From Sale",
            "Sold (bags)", "Sold (kg)", "Returned (bags)", "Returned (kg)",
            "Available (bags)", "Available (kg)", "Return (bags)", "Return (kg)"
        ])
        self.items_table.setRowCount(len(items))

        for row, it in enumerate(items):
            sale_id = int(getattr(it, "sale_id"))
            sale_no = self._sale_no_by_id.get(sale_id, "")

            item_id = int(getattr(it, "id"))
            product_id = int(getattr(it, "product_id"))
            ptype = str(getattr(it, "product_type_snapshot") or "")
            pname = names.get(product_id, f"Product #{product_id}")
            is_fert = self._is_fertilizer_text(ptype)

            sold_bags = int(getattr(it, "qty_bags", 0) or 0)
            sold_kg = float(getattr(it, "total_weight_kg", 0.0) or 0.0)
            returned_bags, returned_kg = sums.get(item_id, (0, 0.0))
            avail_bags = max(0, sold_bags - returned_bags)
            avail_kg = max(0.0, sold_kg - returned_kg)

            def _cell(txt, right=False, bold=False):
                cell = QTableWidgetItem(txt)
                if right:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if bold:
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                return cell

            dash = "-"

            chk = QCheckBox()
            chk.setChecked(False)
            chk.setProperty("sale_id", sale_id)
            chk.setProperty("sale_item_id", item_id)
            self.items_table.setCellWidget(row, 0, chk)

            self.items_table.setItem(row, 1, _cell(pname, bold=True))
            self.items_table.setItem(row, 2, _cell(ptype.title()))
            self.items_table.setItem(row, 3, _cell(str(sale_no)))
            self.items_table.setItem(row, 4, _cell(str(sold_bags), right=True))
            self.items_table.setItem(row, 5, _cell(dash if is_fert else f"{sold_kg:.2f}", right=True))
            self.items_table.setItem(row, 6, _cell(str(returned_bags), right=True))
            self.items_table.setItem(row, 7, _cell(dash if is_fert else f"{returned_kg:.2f}", right=True))
            self.items_table.setItem(row, 8, _cell(str(avail_bags), right=True, bold=True))
            self.items_table.setItem(row, 9, _cell(dash if is_fert else f"{avail_kg:.2f}", right=True, bold=True))

            rb = QSpinBox()
            rb.setRange(0, int(avail_bags))          # clamp: cannot exceed available
            rb.setValue(0)

            rk = QDoubleSpinBox()
            rk.setDecimals(2)
            rk.setRange(0.0, float(avail_kg))        # clamp: cannot exceed available
            rk.setValue(0.0)

            rb.setProperty("remaining_bags", int(avail_bags))
            rk.setProperty("remaining_kg", float(avail_kg))

            self.items_table.setCellWidget(row, 10, rb)
            self.items_table.setCellWidget(row, 11, rk)

            if is_fert:
                rk.setEnabled(False)
                rk.setToolTip("Fertilizer returns are bags-only.")

            nothing_left = (avail_bags <= 0) and (is_fert or avail_kg <= 1e-9)
            if nothing_left:
                chk.setEnabled(False)
                rb.setEnabled(False)
                rk.setEnabled(False)
                chk.setToolTip("Nothing left to return on this line.")

        self.footer.setText(f"Loaded {len(items)} product line(s). Enter what's coming back, then Create Return(s).")

    # ---------------- PURCHASE path ----------------

    def _load_purchases_for_customer(self):
        self._purchase_no_by_id = {}
        cust_id = self.customer_cb.currentData()
        if not cust_id:
            self.footer.setText("No selection target resolved.")
            return

        with get_session() as s:
            q = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == int(cust_id))
                .filter(Purchase.status == "completed")
            )
            if self.mode != "admin":
                q = q.filter(Purchase.created_by_user_id == self.session_user_id)

            rows = q.order_by(Purchase.completed_at.desc(), Purchase.id.desc()).all()
            prod_summary = self._product_summary_for(
                s, PurchaseItem, PurchaseItem.purchase_id, [int(x.id) for x in rows]
            )

        self.sales_table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            pid = int(p.id)
            self._purchase_no_by_id[pid] = str(p.purchase_no or "")

            chk = QCheckBox()
            chk.setChecked(False)
            chk.setProperty("tx_id", pid)
            chk.stateChanged.connect(self._load_items_for_selected_purchases)
            self.sales_table.setCellWidget(r, 0, chk)

            self.sales_table.setItem(r, 1, QTableWidgetItem(str(pid)))
            self.sales_table.setItem(r, 2, QTableWidgetItem(str(p.purchase_no or "")))

            completed_at = getattr(p, "completed_at", None)
            completed_txt = completed_at.strftime("%Y-%m-%d %H:%M") if completed_at else ""
            self.sales_table.setItem(r, 3, QTableWidgetItem(completed_txt))

            total = float(getattr(p, "total_amount", 0) or 0)
            self.sales_table.setItem(r, 4, QTableWidgetItem(f"{total:.2f}"))
            self.sales_table.setItem(r, 5, QTableWidgetItem(str(p.status)))
            self.sales_table.setItem(r, 6, QTableWidgetItem(prod_summary.get(pid, "")))

        if rows:
            self.footer.setText(f"Loaded {len(rows)} completed supply folder files.")
        else:
            self.footer.setText("No completed supply receipts resolved for this selector record.")

    def _load_items_for_selected_purchases(self):
        if self.return_type_cb.currentData() != RETURN_TYPE_PURCHASE:
            return

        purchase_ids = self._selected_tx_ids()
        self.items_table.setRowCount(0)
        if not purchase_ids:
            self.footer.setText("No supply target resolved. Tick rows to map parameters.")
            return

        with get_session() as s:
            items = (
                s.query(PurchaseItem)
                .filter(PurchaseItem.purchase_id.in_(purchase_ids))
                .order_by(PurchaseItem.purchase_id.asc(), PurchaseItem.id.asc())
                .all()
            )

            rows = (
                s.query(
                    PurchaseReturn.purchase_item_id,
                    func.coalesce(func.sum(PurchaseReturn.qty_bags), 0),
                    func.coalesce(func.sum(PurchaseReturn.qty_kg), 0.0),
                )
                .filter(PurchaseReturn.purchase_id.in_(purchase_ids))
                .group_by(PurchaseReturn.purchase_item_id)
                .all()
            )

        sums = {int(item_id): (int(bags or 0), float(kg or 0.0)) for item_id, bags, kg in rows}

        from database.models.product import Product
        product_ids = {int(getattr(it, "product_id")) for it in items}
        with get_session() as s:
            name_rows = (
                s.query(Product.id, Product.name).filter(Product.id.in_(product_ids)).all()
                if product_ids else []
            )
        names = {int(pid): str(pname) for pid, pname in name_rows}

        self.items_table.setHorizontalHeaderLabels([
            "Return?", "Product", "Type", "From Purchase",
            "Bought (bags)", "Bought (kg)", "Returned (bags)", "Returned (kg)",
            "Available (bags)", "Available (kg)", "Return (bags)", "Return (kg)"
        ])
        self.items_table.setRowCount(len(items))

        for row, it in enumerate(items):
            purchase_id = int(getattr(it, "purchase_id"))
            purchase_no = self._purchase_no_by_id.get(purchase_id, "")

            item_id = int(getattr(it, "id"))
            product_id = int(getattr(it, "product_id"))
            ptype = str(getattr(it, "product_type_snapshot") or "")
            pname = names.get(product_id, f"Product #{product_id}")
            is_fert = self._is_fertilizer_text(ptype)

            sold_bags = int(getattr(it, "qty_bags", 0) or 0)
            sold_kg = float(getattr(it, "total_weight_kg", 0.0) or 0.0)
            returned_bags, returned_kg = sums.get(item_id, (0, 0.0))
            avail_bags = max(0, sold_bags - returned_bags)
            avail_kg = max(0.0, sold_kg - returned_kg)

            def _cell(txt, right=False, bold=False):
                cell = QTableWidgetItem(txt)
                if right:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if bold:
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                return cell

            dash = "-"

            chk = QCheckBox()
            chk.setChecked(False)
            chk.setProperty("purchase_id", purchase_id)
            chk.setProperty("purchase_item_id", item_id)
            self.items_table.setCellWidget(row, 0, chk)

            self.items_table.setItem(row, 1, _cell(pname, bold=True))
            self.items_table.setItem(row, 2, _cell(ptype.title()))
            self.items_table.setItem(row, 3, _cell(str(purchase_no)))
            self.items_table.setItem(row, 4, _cell(str(sold_bags), right=True))
            self.items_table.setItem(row, 5, _cell(dash if is_fert else f"{sold_kg:.2f}", right=True))
            self.items_table.setItem(row, 6, _cell(str(returned_bags), right=True))
            self.items_table.setItem(row, 7, _cell(dash if is_fert else f"{returned_kg:.2f}", right=True))
            self.items_table.setItem(row, 8, _cell(str(avail_bags), right=True, bold=True))
            self.items_table.setItem(row, 9, _cell(dash if is_fert else f"{avail_kg:.2f}", right=True, bold=True))

            rb = QSpinBox()
            rb.setRange(0, int(avail_bags))          # clamp: cannot exceed available
            rb.setValue(0)

            rk = QDoubleSpinBox()
            rk.setDecimals(2)
            rk.setRange(0.0, float(avail_kg))        # clamp: cannot exceed available
            rk.setValue(0.0)

            rb.setProperty("remaining_bags", int(avail_bags))
            rk.setProperty("remaining_kg", float(avail_kg))

            self.items_table.setCellWidget(row, 10, rb)
            self.items_table.setCellWidget(row, 11, rk)

            if is_fert:
                rk.setEnabled(False)
                rk.setToolTip("Fertilizer returns are bags-only.")

            nothing_left = (avail_bags <= 0) and (is_fert or avail_kg <= 1e-9)
            if nothing_left:
                chk.setEnabled(False)
                rb.setEnabled(False)
                rk.setEnabled(False)
                chk.setToolTip("Nothing left to return on this line.")

        self.footer.setText(f"Loaded {len(items)} product line(s). Enter what's going back, then Create Return(s).")

    # ---------------- Actions ----------------

    def _print_last_returns(self):
        """Manual, on demand - prints every return from the most recently
        created batch. No auto-print anymore, matching Sales/Purchase/
        Advance payments elsewhere in the app."""
        if not self._last_return_ids:
            QMessageBox.information(
                self, "Print Receipt(s)", "No recently created return(s) to print."
            )
            return
        for rid in self._last_return_ids:
            try:
                print_full_then_optional_delivery(
                    self, self._last_return_doc_type, int(rid))
            except Exception as e:
                QMessageBox.warning(
                    self, "Print Error", f"Could not print return #{rid}:\n{e}"
                )

    def _create_returns(self):
        rtype = self.return_type_cb.currentData()
        reason = (self.reason.toPlainText() or "").strip() or None

        try:
            created_by_user_id = int(state.require_user_id())
        except Exception as e:
            QMessageBox.warning(self, "Login Required", str(e))
            return

        if rtype == RETURN_TYPE_SALES:
            self._create_sales_returns(reason, created_by_user_id)
            return

        if rtype == RETURN_TYPE_PURCHASE:
            self._create_purchase_returns(reason, created_by_user_id)
            return

        QMessageBox.warning(self, "Not Implemented", "This return category is not implemented yet.")

    def _create_sales_returns(self, reason, created_by_user_id: int):
        requests = []

        for row in range(self.items_table.rowCount()):
            chk = self.items_table.cellWidget(row, 0)
            if not chk or not chk.isChecked():
                continue

            sale_id = int(chk.property("sale_id"))
            sale_item_id = int(chk.property("sale_item_id"))

            rb = self.items_table.cellWidget(row, 10)
            rk = self.items_table.cellWidget(row, 11)

            bags = int(rb.value() if rb else 0)
            text_item = self.items_table.item(row, 2)   # column 2 = Type
            ptype = str(text_item.text() if text_item else "")
            kg = float(rk.value() if rk else 0.0)

            remaining_bags = int(rb.property("remaining_bags") if rb else 0)
            remaining_kg = float(rk.property("remaining_kg") if rk else 0.0)
            is_fert = self._is_fertilizer_text(ptype)

            if is_fert:
                if bags <= 0:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Fertilizer return requires bags > 0.")
                    return
                if bags > remaining_bags:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Bags exceed remaining ({remaining_bags}).")
                    return
                kg = 0.0
            else:
                if kg <= 0:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Crops return requires kg > 0.")
                    return
                if kg > remaining_kg + 1e-9:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: KG exceed remaining ({remaining_kg:.2f}).")
                    return
                if bags > remaining_bags:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Bags exceed remaining ({remaining_bags}).")
                    return

            requests.append((sale_id, sale_item_id, kg, bags))

        if not requests:
            QMessageBox.warning(self, "Nothing Selected", "Select at least one item to return.")
            return

        lines = "\n".join([f"- sale_id={sid}, item_id={it}, bags={b}, kg={k:.2f}" for sid, it, k, b in requests])
        ok = QMessageBox.question(
            self,
            "Confirm Returns",
            f"You are about to create {len(requests)} sale return(s):\n\n{lines}\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        created_ids = []
        try:
            for sale_id, sale_item_id, kg, bags in requests:
                rid = ReturnService.create_return(
                    document_type="sale",
                    document_id=int(sale_id),
                    item_id=int(sale_item_id),
                    qty_kg=float(kg),
                    qty_bags=int(bags),
                    reason=reason,
                    created_by_user_id=int(created_by_user_id),
                )
                created_ids.append(int(rid))

                # Auto-approve immediately - the client's own explicit request:
                # no separate "Approve Return" click. This is safe because it
                # was never a separate-reviewer control to begin with (any
                # user who could create a return could already approve it
                # themselves, one extra click later) - it only triggers the
                # cash refund payment, and safely no-ops if nothing was ever
                # paid in cash. Wrapped on its own so a rare failure here
                # doesn't undermine the return itself, which is already
                # safely recorded (stock already adjusted) by this point.
                try:
                    ReturnService.approve_sale_return(
                        return_id=int(rid), created_by_user_id=int(created_by_user_id))
                except Exception as e:
                    QMessageBox.warning(
                        self, "Refund Error",
                        f"Return #{rid} created, but the cash refund step failed:\n{e}"
                    )

        except Exception as e:
            QMessageBox.critical(self, "Return Failed", f"{e}\n\nCreated so far: {created_ids}")
            return

        # No auto-print (client's own request, matching Sales/Purchase/Advance
        # payments earlier) - Print Receipt(s) prints this whole batch on demand.
        self._last_return_ids = list(created_ids)
        self._last_return_doc_type = SALE_RETURN
        self.print_btn.setEnabled(bool(created_ids))

        QMessageBox.information(self, "Success", f"Sale return(s) created: {created_ids}")
        self._load_items_for_selected_sales()

    def _create_purchase_returns(self, reason, created_by_user_id: int):
        requests = []

        for row in range(self.items_table.rowCount()):
            chk = self.items_table.cellWidget(row, 0)
            if not chk or not chk.isChecked():
                continue

            purchase_id = int(chk.property("purchase_id"))
            purchase_item_id = int(chk.property("purchase_item_id"))

            rb = self.items_table.cellWidget(row, 10)
            rk = self.items_table.cellWidget(row, 11)

            bags = int(rb.value() if rb else 0)
            text_item = self.items_table.item(row, 2)   # column 2 = Type
            ptype = str(text_item.text() if text_item else "")
            kg = float(rk.value() if rk else 0.0)

            remaining_bags = int(rb.property("remaining_bags") if rb else 0)
            remaining_kg = float(rk.property("remaining_kg") if rk else 0.0)
            is_fert = self._is_fertilizer_text(ptype)

            if is_fert:
                if bags <= 0:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Fertilizer return requires bags > 0.")
                    return
                if bags > remaining_bags:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Bags exceed remaining ({remaining_bags}).")
                    return
                kg = 0.0
            else:
                if kg <= 0:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Crops return requires kg > 0.")
                    return
                if kg > remaining_kg + 1e-9:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: KG exceed remaining ({remaining_kg:.2f}).")
                    return
                if bags > remaining_bags:
                    QMessageBox.warning(self, "Invalid", f"Row {row+1}: Bags exceed remaining ({remaining_bags}).")
                    return

            requests.append((purchase_id, purchase_item_id, kg, bags))

        if not requests:
            QMessageBox.warning(self, "Nothing Selected", "Select at least one item to return.")
            return

        lines = "\n".join([f"- purchase_id={pid}, item_id={it}, bags={b}, kg={k:.2f}" for pid, it, k, b in requests])
        ok = QMessageBox.question(
            self,
            "Confirm Purchase Returns",
            f"You are about to create {len(requests)} purchase return(s) (refund → goods out):\n\n{lines}\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        created_ids = []
        try:
            for purchase_id, purchase_item_id, kg, bags in requests:
                rid = ReturnService.create_return(
                    document_type="purchase",
                    document_id=int(purchase_id),
                    item_id=int(purchase_item_id),
                    qty_kg=float(kg),
                    qty_bags=int(bags),
                    reason=reason,
                    created_by_user_id=int(created_by_user_id),
                )
                created_ids.append(int(rid))

                # Auto-approve immediately - same reasoning as sale returns
                # above (see comment there): no separate reviewer was ever
                # gated behind this, so removing the extra click loses no
                # real control, and the return itself is already safely
                # recorded regardless of whether this step succeeds.
                try:
                    ReturnService.approve_purchase_return(
                        return_id=int(rid), created_by_user_id=int(created_by_user_id))
                except Exception as e:
                    QMessageBox.warning(
                        self, "Reclaim Error",
                        f"Return #{rid} created, but the cash reclaim step failed:\n{e}"
                    )

        except Exception as e:
            QMessageBox.critical(self, "Purchase Return Failed", f"{e}\n\nCreated so far: {created_ids}")
            return

        # No auto-print (client's own request) - Print Receipt(s) prints
        # this whole batch on demand.
        self._last_return_ids = list(created_ids)
        self._last_return_doc_type = PURCHASE_RETURN
        self.print_btn.setEnabled(bool(created_ids))

        QMessageBox.information(self, "Success", f"Purchase return(s) created: {created_ids}")
        self._load_items_for_selected_purchases()

    # ---------------- Preview dialogs ----------------

    def _open_tx_preview(self, row: int, col: int):
        rtype = self.return_type_cb.currentData()
        chk = self.sales_table.cellWidget(row, 0)
        if not chk:
            return

        tx_id = int(chk.property("tx_id") or 0)
        if tx_id <= 0:
            return

        if rtype == RETURN_TYPE_SALES:
            from apps.product_management_app.features.profiling.views.sale_history_dialog import SaleHistoryDialog
            SaleHistoryDialog(sale_id=tx_id, parent=self).exec_()
            return

        if rtype == RETURN_TYPE_PURCHASE:
            from apps.product_management_app.features.profiling.views.purchase_history_dialog import PurchaseHistoryDialog
            PurchaseHistoryDialog(purchase_id=tx_id, parent=self).exec_()
            return