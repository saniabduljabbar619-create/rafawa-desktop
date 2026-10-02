# apps/product_management_app/features/debt_center/views/sheet_grid.py
"""
A spreadsheet-style grid, as in alg-erp's Master Supplier sheets:

  * rows in the order written, a "#" column, optional computed columns (running balance ...)
  * type in the empty last row to add a row - it is saved as soon as it has what it needs
  * click a cell to change it; remove a row (it stays, crossed out, and stops counting) and bring it back
  * a dark totals row along the bottom that scrolls with the grid

The grid only lays things out. The owner supplies callbacks that talk to the services; any ValueError a
callback raises is shown under the toolbar and the cell goes back to what it was.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from PySide2.QtCore import QDate, QRegularExpression, Qt, QTimer
from PySide2.QtGui import QBrush, QColor, QRegularExpressionValidator
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QPushButton,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from apps.product_management_app.features.debt_center.sheet_style import AMBER, GREEN, MUTED, RED, STYLE

RAW = Qt.UserRole
ORIG = Qt.UserRole + 1
NUMBER_RX = QRegularExpression(r"^[0-9,]*\.?[0-9]*$")
RIGHT = Qt.AlignRight | Qt.AlignVCenter


def show_value(col: Dict[str, Any], raw: Any) -> str:
    """A cell as people read it."""
    if raw in (None, ""):
        return ""
    t = col["type"]
    if t == "money":
        try:
            v = float(str(raw).replace(",", ""))
        except ValueError:
            return str(raw)
        return "" if v == 0 else f"{v:,.2f}"
    if t == "date":
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return str(raw)
    return str(raw)


class _Delegate(QStyledItemDelegate):
    def __init__(self, grid):
        super().__init__(grid.table)
        self.grid = grid

    def _col(self, index):
        return self.grid.col_at(index.column())

    def createEditor(self, parent, option, index):
        c = self._col(index)
        if c is None:
            return None
        t = c["type"]
        if t == "date":
            w = QDateEdit(parent)
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd/MM/yyyy")
            return w
        if t == "choice":
            w = QComboBox(parent)
            w.setEditable(bool(c.get("editable")))
            w.addItem("", "")
            for o in c.get("options", []):
                w.addItem(o, o)
            return w
        w = QLineEdit(parent)
        if t == "money":
            w.setValidator(QRegularExpressionValidator(NUMBER_RX, w))
            w.setAlignment(RIGHT)
        return w

    def setEditorData(self, editor, index):
        raw = index.data(RAW) or ""
        if isinstance(editor, QDateEdit):
            d = QDate.fromString(str(raw)[:10], "yyyy-MM-dd")
            editor.setDate(d if d.isValid() else QDate.currentDate())
        elif isinstance(editor, QComboBox):
            i = editor.findData(raw)
            if i >= 0:
                editor.setCurrentIndex(i)
            else:
                editor.setCurrentText(str(raw))
        else:
            editor.setText(str(raw))

    def setModelData(self, editor, model, index):
        c = self._col(index)
        if isinstance(editor, QDateEdit):
            raw = editor.date().toString("yyyy-MM-dd")
        elif isinstance(editor, QComboBox):
            raw = (editor.currentData() or editor.currentText() or "").strip()
        else:
            raw = editor.text().strip()
            if c["type"] == "money" and raw:
                raw = raw.replace(",", "")
        model.setData(index, raw, RAW)
        model.setData(index, show_value(c, raw), Qt.DisplayRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class SheetGrid(QWidget):
    def __init__(self, columns: List[Dict[str, Any]], computed: List[Dict[str, Any]] = (),
                 first_edit_key: str = "", placeholder_key: str = "", defaults: Optional[Dict[str, Any]] = None,
                 ready: Optional[Callable[[Dict[str, Any]], str]] = None,
                 on_add: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 on_edit: Optional[Callable[[Any, str, str], Any]] = None,
                 on_remove: Optional[Callable[[Any, str], Any]] = None,
                 on_restore: Optional[Callable[[Any], Any]] = None,
                 reload: Optional[Callable[[], Any]] = None, hint: str = ""):
        super().__init__()
        self.setStyleSheet(STYLE)
        self.columns = list(columns)
        self.computed = list(computed)            # [{"label", "key", "width"}] read from row["computed"][key]
        self.first_edit_key = first_edit_key or self.columns[0]["key"]
        self.placeholder_key = placeholder_key
        self.defaults = dict(defaults or {})
        self.ready, self.on_add, self.on_edit = ready, on_add, on_edit
        self.on_remove, self.on_restore, self.reload = on_remove, on_restore, reload
        self.rows: List[Dict[str, Any]] = []
        self.totals: Dict[str, str] = {}
        self._draft: Dict[str, str] = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setVisible(False)
        v.addWidget(self.msg)

        self.table = QTableWidget(0, 0)
        self.table.setObjectName("msSheet")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                   | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setMinimumHeight(44)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.delegate = _Delegate(self)
        self.table.setItemDelegate(self.delegate)
        self.table.itemChanged.connect(self._cell_changed)
        self._pending = None
        self.delegate.closeEditor.connect(lambda *_a: QTimer.singleShot(0, self._apply_pending))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(lambda p: self.row_menu(self.table.viewport().mapToGlobal(p)))
        v.addWidget(self.table, 1)

        self.foot = QTableWidget(1, 0)
        self.foot.setObjectName("msFoot")
        self.foot.horizontalHeader().setVisible(False)
        self.foot.verticalHeader().setVisible(False)
        self.foot.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.foot.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.foot.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.foot.setSelectionMode(QAbstractItemView.NoSelection)
        self.foot.setFocusPolicy(Qt.NoFocus)
        self.foot.setFixedHeight(36)
        self.foot.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().sectionResized.connect(lambda i, _o, n: self.foot.setColumnWidth(i, n))
        self.table.horizontalScrollBar().valueChanged.connect(self.foot.horizontalScrollBar().setValue)
        v.addWidget(self.foot)

        self.hint = QLabel(hint)
        self.hint.setObjectName("msHint")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

    # ------------------------------------------------------------ helpers
    def col_at(self, table_col: int) -> Optional[Dict[str, Any]]:
        return self.columns[table_col - 1] if 1 <= table_col <= len(self.columns) else None

    def say(self, text: str, bad: bool = True) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        self.msg.setStyleSheet(f"color:{RED if bad else GREEN}; font-size:12.5px; font-weight:600;")

    def selected_row(self) -> Optional[Dict[str, Any]]:
        i = self.table.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def _fixed(self, i: int, j: int, text: str, align=None, color=None) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if align is not None:
            it.setTextAlignment(align)
        if color is not None:
            it.setForeground(QBrush(QColor(color)))
        self.table.setItem(i, j, it)
        return it

    # ------------------------------------------------------------ render
    def set_rows(self, rows: List[Dict[str, Any]], totals: Optional[Dict[str, str]] = None) -> None:
        if self.table.state() == QAbstractItemView.EditingState:
            self._pending = (rows, totals)
            return
        self.rows, self.totals = list(rows), dict(totals or {})
        self._render()

    def _apply_pending(self) -> None:
        if self._pending is not None and self.table.state() != QAbstractItemView.EditingState:
            rows, totals = self._pending
            self._pending = None
            self.set_rows(rows, totals)

    def _render(self) -> None:
        t, cols, comp = self.table, self.columns, self.computed
        cur = (t.currentRow(), t.currentColumn())
        n = len(cols) + len(comp) + 1
        t.blockSignals(True)
        t.clear()
        t.setColumnCount(n)
        t.setRowCount(len(self.rows) + 1)
        t.setHorizontalHeaderLabels(["#"] + [c["label"] for c in cols] + [c["label"] for c in comp])
        grey = QColor("#9a958a")
        for i, r in enumerate(self.rows):
            removed = r.get("removed")
            self._fixed(i, 0, str(i + 1), Qt.AlignCenter, MUTED)
            for j, c in enumerate(cols, start=1):
                raw = r["cells"].get(c["key"], "")
                it = QTableWidgetItem(show_value(c, raw))
                it.setData(RAW, raw)
                it.setData(ORIG, raw)
                if c["type"] == "money":
                    it.setTextAlignment(RIGHT)
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    if c.get("tone") and raw not in ("", None, 0, 0.0, "0"):
                        it.setForeground(QBrush(QColor(c["tone"])))
                if removed:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                t.setItem(i, j, it)
            for k, c in enumerate(comp, start=len(cols) + 1):
                it = self._fixed(i, k, r.get("computed", {}).get(c["key"], ""), RIGHT)
                it.setBackground(QColor("#f3f1ec"))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            if r.get("tip"):
                t.item(i, len(cols)).setToolTip(r["tip"])
            if removed:
                for j in range(1, n):
                    it = t.item(i, j)
                    if it:
                        f = it.font()
                        f.setStrikeOut(True)
                        it.setFont(f)
                        it.setForeground(QBrush(grey))
        last = len(self.rows)
        self._fixed(last, 0, "+", Qt.AlignCenter, AMBER)
        for j, c in enumerate(cols, start=1):
            k = c["key"]
            raw = self._draft.get(k, "")
            shown = raw or self.defaults.get(k, "")
            it = QTableWidgetItem(show_value(c, shown) if shown not in ("",) else "")
            it.setData(RAW, raw)
            it.setData(ORIG, raw)
            it.setBackground(QColor("#fffdf8"))
            it.setToolTip("Type here to add a row")
            if not raw:
                if k == self.placeholder_key:
                    it.setText("Type here to add a row")
                if k == self.placeholder_key or k in self.defaults:
                    it.setForeground(QBrush(QColor("#b9b3a6")))
                    f = it.font()
                    f.setItalic(True)
                    it.setFont(f)
            if c["type"] == "money":
                it.setTextAlignment(RIGHT)
            t.setItem(last, j, it)
        for j in range(len(cols) + 1, n):
            self._fixed(last, j, "")
        t.setColumnWidth(0, 44)
        hh = t.horizontalHeader()
        for j, c in enumerate(cols, start=1):
            t.setColumnWidth(j, c.get("width", 140))
            # the main text column takes whatever room is left, so the money columns stay on screen
            hh.setSectionResizeMode(j, QHeaderView.Stretch if c.get("stretch") else QHeaderView.Interactive)
        for k, c in enumerate(comp, start=len(cols) + 1):
            t.setColumnWidth(k, c.get("width", 150))
            hh.setSectionResizeMode(k, QHeaderView.Fixed)
        t.blockSignals(False)
        self._render_foot(n)
        if 0 <= cur[0] < t.rowCount() and 0 <= cur[1] < n:
            t.setCurrentCell(*cur)

    def _render_foot(self, n: int) -> None:
        self.foot.setColumnCount(n)
        keys = [""] + [c["key"] for c in self.columns] + [c["key"] for c in self.computed]
        for j in range(n):
            self.foot.setColumnWidth(j, self.table.columnWidth(j))
            text = self.totals.get(keys[j], "") or ("Totals" if j == 1 else "")
            it = QTableWidgetItem(text)
            it.setForeground(QBrush(QColor("#ffffff")))
            col = self.col_at(j)
            if (col and col["type"] == "money") or j > len(self.columns):
                it.setTextAlignment(RIGHT)
            self.foot.setItem(0, j, it)

    # ------------------------------------------------------------ saving
    def _cell_changed(self, item: QTableWidgetItem) -> None:
        c = self.col_at(item.column())
        if c is None:
            return
        raw, orig = item.data(RAW) or "", item.data(ORIG) or ""
        if raw == orig:
            return
        self.table.blockSignals(True)
        item.setData(ORIG, raw)                 # the editor sets RAW then the text: only act once
        self.table.blockSignals(False)
        self.say("")
        if item.row() < len(self.rows):
            row = self.rows[item.row()]
            self._run(lambda: self.on_edit(row["id"], c["key"], raw))
            return
        self._draft[c["key"]] = raw
        missing = self.ready(dict(self._draft)) if self.ready else ""
        if not missing:
            values, self._draft = dict(self._draft), {}
            self._run(lambda: self.on_add(values), on_fail=lambda: self._draft.update(values))
            return
        self.say(missing, bad=False)

    def _run(self, fn, on_fail=None) -> None:
        try:
            fn()
        except ValueError as e:
            if on_fail:
                on_fail()
            self.say(str(e))
        except Exception as e:                       # a database error must not kill the screen
            if on_fail:
                on_fail()
            self.say(f"Not saved: {e}")
        QTimer.singleShot(0, self._refresh_now)

    def _refresh_now(self) -> None:
        if self.reload:
            self.reload()

    # ------------------------------------------------------------ actions
    def add_row(self) -> None:
        last = len(self.rows)
        j = next(j for j, c in enumerate(self.columns, start=1) if c["key"] == self.first_edit_key)
        self.table.setCurrentCell(last, j)
        self.table.scrollToItem(self.table.item(last, j))
        self.table.setFocus()
        self.table.editItem(self.table.item(last, j))

    def row_menu(self, pos) -> None:
        r = self.selected_row()
        m = QMenu(self)
        if r is None:
            m.addAction("Click a row first").setEnabled(False)
        elif r.get("removed"):
            m.addAction("Bring the row back").triggered.connect(lambda: self._run(lambda: self.on_restore(r["id"])))
        else:
            m.addAction("Remove row…").triggered.connect(lambda: self.remove(r))
        m.exec_(pos)

    def remove(self, r: Optional[Dict[str, Any]] = None) -> None:
        r = r or self.selected_row()
        if r is None:
            self.say("Click the row you want to remove first.")
            return
        if r.get("removed"):
            return
        reason, ok = QInputDialog.getText(
            self, "Remove row",
            "Remove this row? It stays in the sheet, crossed out, and stops counting.\n\nWhy? (optional)")
        if ok:
            self._run(lambda: self.on_remove(r["id"], reason.strip()))

    def restore(self) -> None:
        r = self.selected_row()
        if r is None or not r.get("removed"):
            self.say("Click a removed (crossed-out) row first.")
            return
        self._run(lambda: self.on_restore(r["id"]))


def today_iso() -> str:
    return date.today().isoformat()
