# apps/product_management_app/features/admin_users/views/drawer_screen.py
# -*- coding: utf-8 -*-
"""
Per-user cash-drawer accountability screen (Phase 3).

Replaces UserProfileDialog. This screen holds NO money logic - DrawerService
derives everything, including the bilingual sentences. This file only paints
and routes the Settle click back to the service.

DrawerPanel is the actual UI (a QWidget) for one user's drawer. DrawerScreen
wraps it as a modal QDialog for the admin "pick a user, inspect their
drawer" flow. CashierDailySummaryView (cashier/views/cashier_daily_summary.py)
embeds DrawerPanel directly, un-shelled, pinned to the logged-in cashier's
own user_id - same screen, no dialog chrome, no user picker.
"""
from __future__ import annotations

from datetime import date as _date, datetime
from PySide2.QtCore import Qt, QDate
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDateEdit,
    QCheckBox, QComboBox, QScrollArea, QFrame, QPushButton
)

from database.services.drawer_service import DrawerService
from database.services.user_admin_service import UserAdminService
from shared.auth.session import require_user_id, get_session as get_user_session
from apps.product_management_app.features.admin_users.views.drawer_widgets import (
    MoneyCard, StatCard, VerdictRow, Banner, money, GREEN, RED, GREY, INK, LINE
)

# OPEN DECISION (handoff sec.7): who may confirm cash physically changed hands.
# True = admin only. Flip this one line if the user wants cashiers to settle.
SETTLE_ADMIN_ONLY = True

# Same bucket vocabulary the engine uses for both cash_events and the
# cross-cashier settlement lines - one label map, used everywhere a row
# needs to say WHICH of the four reconciliation categories it belongs to.
_BUCKET_LABELS = {
    "purchase": "Purchase",
    "sale": "Sales",
    "advance_in": "Advance IN",
    "advance_out": "Advance OUT",
    "other": "Other",
}

# Split 1 - cash you actually handled (labels unchanged from before this
# refinement, per the client's own instruction: these already read fine).
_SPLIT1_CARDS = [
    ("total_paid_sales", "Sales money you handled"),
    ("total_paid_purchase", "Purchase money you handled"),
    ("total_paid_advance_in", "Advance IN you handled"),
    ("total_paid_advance_out", "Advance OUT you handled"),
]

# Split 2 - the SAME settlement figure as the "settled with cashiers" card
# above, broken down by which document type caused it. New this round.
_SPLIT2_CARDS = [
    ("purchase_settlement_total", "Purchase settlement (for other cashiers)"),
    ("sale_settlement_total", "Sales settlement (for other cashiers)"),
    ("advance_in_settlement_total", "Advance IN settlement (for other cashiers)"),
    ("advance_out_settlement_total", "Advance OUT settlement (for other cashiers)"),
]

# Which cash_events bucket(s) + which side (False=mine/Split1, True=cross/
# Split2) each stat card represents - drives the click-to-detail drilldown.
# Every one of these lives entirely in cash_events (bucket + cross fields
# already carried on each row), so no new engine query is needed here.
_CARD_FILTERS = {
    "total_paid_sales": ({"sale"}, False),
    "total_paid_purchase": ({"purchase"}, False),
    "total_paid_advance_in": ({"advance_in"}, False),
    "total_paid_advance_out": ({"advance_out"}, False),
    "purchase_settlement_total": ({"purchase"}, True),
    "sale_settlement_total": ({"sale"}, True),
    "advance_in_settlement_total": ({"advance_in"}, True),
    "advance_out_settlement_total": ({"advance_out"}, True),
}


class DrawerPanel(QWidget):
    """
    The entire cash-drawer accountability UI for ONE user - cards, splits,
    verdicts, table, drilldowns. Everything below was DrawerScreen's own
    body until this refactor; DrawerScreen (further down) is now just a
    QDialog shell that embeds this, and CashierDailySummaryView embeds it
    a second time, un-shelled, pinned to whoever is logged in. Same class,
    same reload()/_fill_table()/etc - one engine, two frames around it.
    """

    def __init__(self, user_id: int, parent=None):
        super().__init__(parent)
        self.user_id = int(user_id or 0)
        self.lang = "en"
        # Defaults to TODAY, not "no filter" (which meant every payment
        # ever recorded - exactly the client's "shows everything" complaint).
        # A fresh day correctly starts at zero and fills in as it happens;
        # the operator widens the range explicitly via the checkbox below.
        # Reconciliation (i_owe/owed_to_me/verdicts) is NOT affected by this -
        # DrawerService keeps that global regardless of this date range.
        self.start_date = _date.today()
        self.end_date = _date.today()
        self._verdict_rows = []
        self._rows_data = []
        self._last_cash_events = []
        self._last_old_settlements = []
        self._last_cross_lines = []

        _id, username, role, _active = UserAdminService.get_user_header(self.user_id)
        self.setObjectName("DrawerPanel")
        self.setStyleSheet("QWidget#DrawerPanel { background: #f3f4f6; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # ---------------- header (pinned, never scrolls) --------------
        head = QHBoxLayout()
        title = QLabel(f"{username}  \u2022  {role}")
        title.setStyleSheet(f"font-size: 18px; font-weight: 900; color: {INK};")
        head.addWidget(title)
        head.addStretch(1)

        self.lang_cb = QComboBox()
        self.lang_cb.addItem("English", "en")
        self.lang_cb.addItem("Hausa", "ha")
        self.lang_cb.currentIndexChanged.connect(self._on_lang)
        head.addWidget(self.lang_cb)

        self.date_chk = QCheckBox("Filter by date")
        self.date_chk.stateChanged.connect(self._on_dates)
        head.addWidget(self.date_chk)

        self.d_from = QDateEdit()
        self.d_from.setCalendarPopup(True)
        self.d_from.setDisplayFormat("yyyy-MM-dd")
        self.d_from.setDate(QDate.currentDate())
        self.d_from.setMinimumWidth(115)
        self.d_from.setEnabled(False)
        self.d_from.dateChanged.connect(self._on_dates)
        head.addWidget(self.d_from)

        self.d_to = QDateEdit()
        self.d_to.setCalendarPopup(True)
        self.d_to.setDisplayFormat("yyyy-MM-dd")
        self.d_to.setDate(QDate.currentDate())
        self.d_to.setMinimumWidth(115)
        self.d_to.setEnabled(False)
        self.d_to.dateChanged.connect(self._on_dates)
        head.addWidget(self.d_to)
        outer.addLayout(head)

        # ---------------- everything else scrolls ----------------------
        # Cards + splits + table all live in `content`, wrapped in a resizable
        # QScrollArea. At smaller window sizes (or a growing table), the whole
        # assembly scrolls as one unit rather than clipping silently.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # ---------------- banner + headline money --------------------
        self.banner = Banner()
        root.addWidget(self.banner)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_in = MoneyCard("", 0, GREEN)
        self.card_out = MoneyCard("", 0, RED)
        self.card_settled = MoneyCard("", 0, GREY)
        self.card_net = MoneyCard("", 0, INK)
        cards.addWidget(self.card_in)
        cards.addWidget(self.card_out)
        cards.addWidget(self.card_settled)
        cards.addWidget(self.card_net)
        root.addLayout(cards)

        # ---------------- what to do now -----------------------------
        self.todo_lbl = QLabel("WHAT TO DO NOW")
        self.todo_lbl.setStyleSheet(
            f"color: {GREY}; font-size: 11px; font-weight: 800;")
        root.addWidget(self.todo_lbl)

        self.todo_host = QWidget()
        self.todo_lay = QVBoxLayout(self.todo_host)
        self.todo_lay.setContentsMargins(0, 0, 0, 0)
        self.todo_lay.setSpacing(6)
        root.addWidget(self.todo_host)

        # ---------------- Split 1: cash you handled -------------------
        self.split1_lbl = QLabel("CASH YOU HANDLED")
        self.split1_lbl.setStyleSheet(
            f"color: {GREY}; font-size: 11px; font-weight: 800;")
        root.addWidget(self.split1_lbl)

        s1_details = QHBoxLayout()
        s1_details.setSpacing(6)
        self.stat_cards = {}
        for key, cap in _SPLIT1_CARDS:
            c = StatCard(cap, 0)
            self.stat_cards[key] = c
            s1_details.addWidget(c)
        root.addLayout(s1_details)

        s1_totals = QHBoxLayout()
        s1_totals.setSpacing(10)
        self.card_net_in = MoneyCard("Total Net IN (Sales + Advance IN)", 0, GREEN)
        self.card_net_out = MoneyCard("Total Net OUT (Purchase + Advance OUT)", 0, RED)
        s1_totals.addWidget(self.card_net_in)
        s1_totals.addWidget(self.card_net_out)
        root.addLayout(s1_totals)

        # ---------------- Split 2: settlement, broken down by type ----
        self.split2_lbl = QLabel("SETTLEMENT WITH OTHER CASHIERS, BY TYPE")
        self.split2_lbl.setStyleSheet(
            f"color: {GREY}; font-size: 11px; font-weight: 800;")
        root.addWidget(self.split2_lbl)

        s2_details = QHBoxLayout()
        s2_details.setSpacing(6)
        for key, cap in _SPLIT2_CARDS:
            c = StatCard(cap, 0)
            self.stat_cards[key] = c
            s2_details.addWidget(c)
        root.addLayout(s2_details)

        s2_totals = QHBoxLayout()
        s2_totals.setSpacing(10)
        self.card_settlement_in = MoneyCard("Total Settlement IN (Sales + Advance IN)", 0, GREEN)
        self.card_settlement_out = MoneyCard("Total Settlement OUT (Purchase + Advance OUT)", 0, RED)
        s2_totals.addWidget(self.card_settlement_in)
        s2_totals.addWidget(self.card_settlement_out)
        root.addLayout(s2_totals)

        # ---------------- toggle + table -----------------------------
        tog = QHBoxLayout()
        self.toggle = QCheckBox("Show settlements other people did for me")
        self.toggle.stateChanged.connect(self.reload)
        tog.addWidget(self.toggle)

        self.btn_party_totals = QPushButton("Total Purchases & Sales by Party")
        self.btn_party_totals.clicked.connect(self._open_party_totals)
        tog.addWidget(self.btn_party_totals)

        tog.addStretch(1)
        self.hint = QLabel("Double-click a row for the full detail.")
        self.hint.setStyleSheet(f"color: {GREY}; font-size: 11px;")
        tog.addWidget(self.hint)
        root.addLayout(tog)

        self.table = QTableWidget(0, 8)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._detail)
        self.table.setStyleSheet(
            "QTableWidget { background: #ffffff; border: 1px solid " + LINE +
            "; border-radius: 8px; }")
        # ~10 rows visible by default; beyond that the table's OWN scrollbar
        # takes over rather than the whole dialog growing without bound.
        self.table.setMinimumHeight(380)
        root.addWidget(self.table, 1)

        self.legend = QLabel("\U0001F7E1 Amber row = old settlement - money that moved today for something recorded on an earlier day.")
        self.legend.setStyleSheet(f"color: {GREY}; font-size: 11px;")
        root.addWidget(self.legend)

        # ---------------- every card is clickable, drills to detail ----
        # Money you collected/paid out now filter by the SAME buckets their
        # own card formula uses (money_collected = sale + advance_in;
        # money_paid_out = purchase + advance_out), not raw direction. A
        # raw direction filter let a sales-refund (bucket="sale",
        # direction="out") wrongly appear under "Money you paid out" - it
        # belongs only in Sales Return history - and it hid a purchase-
        # reclaim (bucket="purchase", direction="in") from that same view,
        # even though that reclaim is exactly what caused the reduction.
        self.card_in.clicked.connect(
            lambda: self._show_cash_events_detail("Money you collected", buckets={"sale", "advance_in"}))
        self.card_out.clicked.connect(
            lambda: self._show_cash_events_detail("Money you paid out", buckets={"purchase", "advance_out"}))
        self.card_settled.clicked.connect(
            lambda: self._show_cross_lines_detail("Settled with other cashiers", status="settled"))
        self.card_net.clicked.connect(
            lambda: self._show_cash_events_detail("Everything behind your net position"))

        for key, cap in _SPLIT1_CARDS:
            buckets, cross = _CARD_FILTERS[key]
            self.stat_cards[key].clicked.connect(
                lambda buckets=buckets, cross=cross, cap=cap:
                self._show_cash_events_detail(cap, buckets=buckets, cross=cross))
        for key, cap in _SPLIT2_CARDS:
            buckets, cross = _CARD_FILTERS[key]
            self.stat_cards[key].clicked.connect(
                lambda buckets=buckets, cross=cross, cap=cap:
                self._show_cash_events_detail(cap, buckets=buckets, cross=cross))

        self.card_net_in.clicked.connect(
            lambda: self._show_cash_events_detail(
                "Total Net IN (Sales + Advance IN)", buckets={"sale", "advance_in"}, cross=False))
        self.card_net_out.clicked.connect(
            lambda: self._show_cash_events_detail(
                "Total Net OUT (Purchase + Advance OUT)", buckets={"purchase", "advance_out"}, cross=False))
        self.card_settlement_in.clicked.connect(
            lambda: self._show_cash_events_detail(
                "Total Settlement IN (Sales + Advance IN)", buckets={"sale", "advance_in"}, cross=True))
        self.card_settlement_out.clicked.connect(
            lambda: self._show_cash_events_detail(
                "Total Settlement OUT (Purchase + Advance OUT)", buckets={"purchase", "advance_out"}, cross=True))

        self.reload()

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def _on_lang(self):
        self.lang = self.lang_cb.currentData() or "en"
        self.reload()

    def _on_dates(self):
        on = self.date_chk.isChecked()
        self.d_from.setEnabled(on)
        self.d_to.setEnabled(on)
        if on:
            self.start_date = self.d_from.date().toPython()
            self.end_date = self.d_to.date().toPython()
        else:
            # Unchecking returns to TODAY, not "no filter" (all-time) -
            # matches the screen's own default state.
            self.start_date = _date.today()
            self.end_date = _date.today()
        self.reload()

    def _can_settle(self) -> bool:
        if not SETTLE_ADMIN_ONLY:
            return True
        s = get_user_session()
        return str(getattr(s, "role", "") or "").strip().lower() in ("admin", "pm")

    # ------------------------------------------------------------------
    # paint
    # ------------------------------------------------------------------
    def _open_party_totals(self):
        from apps.product_management_app.features.admin_users.dialogs.party_totals_dialog import (
            PartyTotalsDialog,
        )
        dlg = PartyTotalsDialog(user_id=self.user_id, parent=self)
        dlg.exec_()

    def reload(self):
        try:
            # Table rows (cross_settlements.lines) only narrow when the
            # operator has EXPLICITLY ticked "Filter by date" - the default
            # (unchecked) state leaves them showing full history, even
            # though the cards above still default to today. Same checkbox,
            # same date pickers, two different default behaviors.
            table_start = self.start_date if self.date_chk.isChecked() else None
            table_end = self.end_date if self.date_chk.isChecked() else None
            data = DrawerService.compute(
                self.user_id, self.start_date, self.end_date,
                table_start_date=table_start, table_end_date=table_end)
        except Exception as e:
            QMessageBox.critical(self, "Drawer", f"Could not load the drawer:\n{e}")
            return

        h = data["human"]
        c = data["cards"]
        lang = self.lang
        self._last_cash_events = data["cash_events"]
        self._last_old_settlements = data["old_settlements"]
        self._last_cross_lines = data["cross_settlements"]["lines"]

        self.banner.paint(h["status_en"] if lang == "en" else h["status_ha"],
                          bool(h["is_balanced"]))

        lbl = h["labels"]
        self.card_in.set_values(lbl["money_collected"][lang], h["money_collected"], GREEN)
        self.card_out.set_values(lbl["money_paid_out"][lang], h["money_paid_out"], RED)

        # cash that already moved drawer-to-drawer: shown as its own term so
        # the three cards above visibly add up to the net below.
        setl = float(h["settled_with_cashiers"] or 0)
        self.card_settled.set_values(
            lbl["settled_with_cashiers"][lang], abs(setl),
            GREEN if setl > 0.005 else (RED if setl < -0.005 else GREY))

        # No minus signs: the label says the direction, the number stays plain.
        # Signed net_position is still in cards[] for the admin/accountant view.
        d = h["net_direction"]
        self.card_net.set_values(
            lbl["net_position"][lang], h["net_position_abs"],
            GREEN if d == "holding" else (RED if d == "released" else GREY))

        # Split 1 and Split 2 are both plain, non-negative sums by
        # construction (Split 1 = I created it and settled it myself; Split 2
        # = someone else created it, I settled it) - no sign-flip color logic
        # needed here, unlike net_position/settled_with_cashiers above, which
        # genuinely can point either way between two cashiers.
        for key, _cap in _SPLIT1_CARDS:
            self.stat_cards[key].set_amount(abs(float(c.get(key, 0) or 0)))
        for key, _cap in _SPLIT2_CARDS:
            self.stat_cards[key].set_amount(abs(float(c.get(key, 0) or 0)))

        self.card_net_in.set_values(
            "Total Net IN (Sales + Advance IN)", abs(float(c.get("total_net_in", 0) or 0)))
        self.card_net_out.set_values(
            "Total Net OUT (Purchase + Advance OUT)", abs(float(c.get("total_net_out", 0) or 0)))
        self.card_settlement_in.set_values(
            "Total Settlement IN (Sales + Advance IN)", abs(float(c.get("total_settlement_in", 0) or 0)))
        self.card_settlement_out.set_values(
            "Total Settlement OUT (Purchase + Advance OUT)", abs(float(c.get("total_settlement_out", 0) or 0)))

        self._paint_verdicts(h["verdicts"])
        self._fill_table(data)

    def _paint_verdicts(self, verdicts):
        for w in self._verdict_rows:
            w.setParent(None)
            w.deleteLater()
        self._verdict_rows = []

        if not verdicts:
            self.todo_lbl.setVisible(False)
            self.todo_host.setVisible(False)
            return

        self.todo_lbl.setVisible(True)
        self.todo_host.setVisible(True)
        can = self._can_settle()
        for v in verdicts:
            row = VerdictRow(v, self.lang, can_settle=can, parent=self.todo_host)
            row.settle_clicked.connect(self._settle)
            self.todo_lay.addWidget(row)
            self._verdict_rows.append(row)

    def _fill_table(self, data):
        showing_others = self.toggle.isChecked()
        if showing_others:
            # settlements OTHER people did on this user's documents
            rows = [ln for ln in data["cross_settlements"]["lines"]
                    if ln.get("creator_id") == self.user_id
                    and ln.get("settler_id") != self.user_id]
            heads = ["When", "Type", "Document", "Amount", "Party",
                     "Settled by", "Status", "Settled on"]
        else:
            rows = data["cash_events"]
            heads = ["When", "Type", "Document", "In/Out", "Amount",
                     "Method", "Party", "Document made by"]

        self._rows_data = rows
        self.table.clear()
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.setRowCount(len(rows))

        for r, d in enumerate(rows):
            when = d.get("when")
            when_s = when.strftime("%Y-%m-%d %H:%M") if when else ""
            type_label = _BUCKET_LABELS.get(d.get("bucket"), d.get("entity_type", ""))
            is_old = (not showing_others) and bool(d.get("is_old_settlement"))
            if is_old:
                type_label = f"{type_label} (Old settlement)"
            if showing_others:
                st = str(d.get("status") or "open")
                son = d.get("settled_at")
                cells = [when_s, type_label, d.get("doc_no", ""),
                         money(d.get("amount")), d.get("party", ""),
                         d.get("settler", ""), st,
                         son.strftime("%Y-%m-%d") if son else ""]
            else:
                cells = [when_s, type_label, d.get("doc_no", ""),
                         d.get("direction", ""), money(d.get("amount")),
                         d.get("method", ""), d.get("party", ""),
                         d.get("doc_creator", "")]
            for col, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                if showing_others and str(d.get("status")) == "settled":
                    it.setForeground(Qt.gray)   # settled = greyed history
                if is_old:
                    # Soft amber - "this money moved today, but for
                    # something recorded on an earlier day" - distinct
                    # from a plain white row (a genuinely same-day
                    # transaction), so old and new settlements are never
                    # visually confused with each other.
                    it.setBackground(QColor("#fff3cd"))
                self.table.setItem(r, col, it)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # An empty table with a non-zero net reads like an accusation with no
        # evidence. Say where the evidence actually is.
        if not rows and not showing_others:
            self.hint.setText("You did not handle any cash yourself. "
                              "Tick the box on the left to see what other "
                              "cashiers settled for you.")
            self.hint.setStyleSheet(f"color: {RED}; font-size: 12px; font-weight: 700;")
        else:
            self.hint.setText("Double-click a row for the full detail.")
            self.hint.setStyleSheet(f"color: {GREY}; font-size: 11px;")

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _settle(self, other_user_id: int):
        other_user_id = int(other_user_id or 0)
        if other_user_id <= 0:
            return
        name = ""
        for w in self._verdict_rows:
            if int(w.v.get("other_user_id") or 0) == other_user_id:
                name = w.v.get("other_user") or ""
                break
        if QMessageBox.question(
                self, "Confirm settlement",
                f"Has the cash between this user and {name} actually changed hands?\n\n"
                "Only confirm AFTER the money has physically moved.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            res = DrawerService.settle_with(self.user_id, other_user_id, require_user_id())
        except Exception as e:
            QMessageBox.critical(self, "Settle failed", str(e))
            return
        if not res.get("ok"):
            QMessageBox.warning(self, "Settle", f"Nothing settled ({res.get('error')}).")
            return
        QMessageBox.information(self, "Settled",
                                f"Marked {res.get('settled', 0)} item(s) settled.")
        self.reload()

    def _detail(self, row: int, _col: int):
        if row < 0 or row >= len(self._rows_data):
            return
        d = self._rows_data[row]
        lines = []
        for k, v in d.items():
            if k in ("when", "settled_at") and v:
                v = v.strftime("%Y-%m-%d %H:%M")
            lines.append(f"{k}: {v}")
        QMessageBox.information(self, "Detail", "\n".join(lines))

    # ------------------------------------------------------------------
    # Card drilldown - every card is clickable, showing exactly which
    # transactions produced the number on it. Reconciliation only means
    # something if you can trust the total, and the only way to trust a
    # total is being able to see what it's made of.
    # ------------------------------------------------------------------
    def _show_cash_events_detail(self, title: str, buckets=None, cross=None, direction=None):
        rows = []
        for ev in self._last_cash_events:
            if buckets is not None and ev.get("bucket") not in buckets:
                continue
            if cross is not None and bool(ev.get("cross")) != cross:
                continue
            if direction is not None and ev.get("direction") != direction:
                continue
            rows.append(ev)

        # Old settlements collected in this same period, against an older
        # document - never part of the card's own total (that stays
        # document-date-attributed so returns keep netting correctly), but
        # surfaced here so the "full story" behind the card is visible: real
        # cash that moved in this window, even from a document that didn't.
        for ev in self._last_old_settlements:
            if buckets is not None and ev.get("bucket") not in buckets:
                continue
            if cross is not None and bool(ev.get("cross")) != cross:
                continue
            if direction is not None and ev.get("direction") != direction:
                continue
            tagged = dict(ev)
            tagged["is_old_settlement"] = True
            rows.append(tagged)

        rows.sort(key=lambda r: r.get("when") or datetime.min, reverse=True)
        self._show_detail_rows(title, rows, kind="cash_events")

    def _show_cross_lines_detail(self, title: str, status=None):
        rows = []
        for ln in self._last_cross_lines:
            if status is not None and ln.get("status") != status:
                continue
            rows.append(ln)
        self._show_detail_rows(title, rows, kind="cross_lines")

    def _show_detail_rows(self, title: str, rows: list, kind: str):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(820, 480)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet(f"font-size:15px; font-weight:800; color:{INK};")
        lay.addWidget(heading)

        total = sum(float(r.get("amount") or 0) for r in rows)
        sub = QLabel(f"{len(rows)} transaction(s)  \u2022  total {money(total)}")
        sub.setStyleSheet(f"color:{GREY}; font-size:12px;")
        lay.addWidget(sub)

        if kind == "cash_events":
            heads = ["When", "Type", "Document", "In/Out", "Amount", "Method", "Party", "Document made by", "Note"]
        else:
            heads = ["When", "Type", "Document", "Amount", "Party", "Creator", "Settler", "Status"]

        table = QTableWidget(len(rows), len(heads))
        table.setHorizontalHeaderLabels(heads)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for r, d in enumerate(rows):
            when = d.get("when")
            when_s = when.strftime("%Y-%m-%d %H:%M") if when else ""
            type_label = _BUCKET_LABELS.get(d.get("bucket"), d.get("entity_type", ""))
            is_old = bool(d.get("is_old_settlement"))
            if kind == "cash_events":
                if is_old:
                    doc_dt = d.get("doc_created_at")
                    doc_dt_s = doc_dt.strftime("%Y-%m-%d") if doc_dt else "an earlier date"
                    note = f"Old settlement \u2014 originally from {d.get('doc_no','')}, created {doc_dt_s}"
                else:
                    note = ""
                cells = [when_s, type_label, d.get("doc_no", ""), d.get("direction", ""),
                        money(d.get("amount")), d.get("method", ""), d.get("party", ""),
                        d.get("doc_creator", ""), note]
            else:
                cells = [when_s, type_label, d.get("doc_no", ""), money(d.get("amount")),
                        d.get("party", ""), d.get("creator", ""), d.get("settler", ""),
                        str(d.get("status") or "open")]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if is_old:
                    item.setBackground(QColor("#fff8e1"))
                    f = item.font()
                    f.setItalic(True)
                    item.setFont(f)
                table.setItem(r, col, item)

        lay.addWidget(table, 1)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        row_btn = QHBoxLayout()
        row_btn.addStretch(1)
        row_btn.addWidget(btn_close)
        lay.addLayout(row_btn)

        dlg.exec_()


# ==========================================================================
# Modal shell around DrawerPanel - the admin "pick a user, inspect their
# drawer" entry point. Public signature (user_id, parent) is unchanged from
# before this refactor, so every existing caller keeps working as-is; all
# the actual UI/logic now lives in DrawerPanel above.
# ==========================================================================
class DrawerScreen(QDialog):

    def __init__(self, user_id: int, parent=None):
        super().__init__(parent)
        user_id = int(user_id or 0)
        _id, username, role, _active = UserAdminService.get_user_header(user_id)

        self.setWindowTitle(f"Cash Drawer - {username}")
        self.setModal(True)
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
        )
        self.resize(1150, 820)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.panel = DrawerPanel(user_id, parent=self)
        lay.addWidget(self.panel)