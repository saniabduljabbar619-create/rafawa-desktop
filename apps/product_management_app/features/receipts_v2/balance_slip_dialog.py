# apps/product_management_app/features/receipts_v2/balance_slip_dialog.py
# -*- coding: utf-8 -*-
"""
"Print balance" - pick what to print (this document's balance, the total still pending, or both) and the
language (Hausa / English), see the slip as it will come out, then print it.

    open_balance_slip(parent, "sale" | "purchase" | "advance" | "payment", id)
    open_party_balance_slip(parent, party_id, walkin_name="")        # total only

The last language used is remembered on this PC.
"""
from __future__ import annotations

import json
import logging

from PySide2.QtGui import QFont
from PySide2.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from database.services.balance_statement_service import BalanceStatementService
from shared.paths import data_dir

from .balance_slip import (
    DEFAULT_LANG, LANGUAGES, SCOPE_BOTH, SCOPE_CURRENT, SCOPE_TOTAL, render_balance_slip,
)
from .receipt_models import BOLD_MARKER
from .receipt_print import print_text
from .shop import get_shop

log = logging.getLogger("rafawa.balance_slip")
_PREFS = "print_prefs.json"


def _load_lang() -> str:
    try:
        lang = json.loads((data_dir() / _PREFS).read_text(encoding="utf-8")).get("balance_lang", DEFAULT_LANG)
        return lang if lang in LANGUAGES else DEFAULT_LANG
    except Exception:
        return DEFAULT_LANG


def _save_lang(lang: str) -> None:
    try:
        path = data_dir() / _PREFS
        try:
            prefs = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            prefs = {}
        prefs["balance_lang"] = lang
        path.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        log.exception("Could not remember the balance slip language")


class BalanceSlipDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.data = data
        self.setWindowTitle(f"Print balance - {data.get('name') or ''}")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)

        pend = data["pending"]
        owe = []
        if pend["they_owe_us"]:
            owe.append(f"owes us \u20a6{pend['they_owe_us']:,.2f}")
        if pend["we_owe_them"]:
            owe.append(f"we owe \u20a6{pend['we_owe_them']:,.2f}")
        head = QLabel(f"<b>{data.get('name') or ''}</b>" + (f" - {data['ref']}" if data.get("ref") else "")
                      + (f"<br>Pending: {'; '.join(owe)}" if owe else "<br>Nothing pending for this account."))
        lay.addWidget(head)

        form = QFormLayout()
        self.scope_cb = QComboBox()
        if data.get("current"):
            kind_word = {"sale": "sale", "purchase": "purchase", "advance": "advance"}[data["kind"]]
            self.scope_cb.addItem(f"Current balance of this {kind_word}", SCOPE_CURRENT)
        self.scope_cb.addItem("Total balance (all pending: unpaid / part-paid / unused advance)", SCOPE_TOTAL)
        if data.get("current"):
            self.scope_cb.addItem("Both", SCOPE_BOTH)
        # the total only makes sense when something is pending
        if not data.get("has_pending"):
            idx = self.scope_cb.findData(SCOPE_TOTAL)
            self.scope_cb.model().item(idx).setEnabled(False)
            both = self.scope_cb.findData(SCOPE_BOTH)
            if both >= 0:
                self.scope_cb.model().item(both).setEnabled(False)
        self.lang_cb = QComboBox()
        for key, label in LANGUAGES.items():
            self.lang_cb.addItem(label, key)
        self.lang_cb.setCurrentIndex(max(self.lang_cb.findData(_load_lang()), 0))
        form.addRow("Print:", self.scope_cb)
        form.addRow("Language:", self.lang_cb)
        lay.addLayout(form)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Courier New")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.preview.setFont(font)
        self.preview.setMinimumHeight(340)
        lay.addWidget(self.preview, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_print = QPushButton("Print")
        self.btn_print.setObjectName("PrimaryButton")
        self.btn_close = QPushButton("Close")
        row.addWidget(self.btn_print)
        row.addWidget(self.btn_close)
        lay.addLayout(row)

        self.scope_cb.currentIndexChanged.connect(self._refresh)
        self.lang_cb.currentIndexChanged.connect(self._refresh)
        self.btn_print.clicked.connect(self._print)
        self.btn_close.clicked.connect(self.reject)
        self._refresh()

    # ------------------------------------------------------------------
    def scope(self) -> str:
        return self.scope_cb.currentData()

    def lang(self) -> str:
        return self.lang_cb.currentData()

    def slip_text(self) -> str:
        return render_balance_slip(self.data, get_shop(), self.lang(), self.scope())

    def _refresh(self):
        self.preview.setPlainText(self.slip_text().replace(BOLD_MARKER, ""))

    def _print(self):
        _save_lang(self.lang())
        try:
            print_text(self, self.slip_text(), window_title=f"Balance - {self.data.get('ref') or self.data.get('name')}")
        except Exception as e:
            QMessageBox.warning(self, "Print Error", f"Could not print:\n{e}")


def _open(parent, data: dict) -> None:
    BalanceSlipDialog(data, parent).exec_()


def open_balance_slip(parent, kind: str, entity_id: int) -> None:
    try:
        data = BalanceStatementService.for_document(kind, int(entity_id))
    except Exception as e:
        QMessageBox.warning(parent, "Print balance", str(e))
        return
    _open(parent, data)


def open_party_balance_slip(parent, party_id: int, walkin_name: str = "") -> None:
    try:
        data = BalanceStatementService.for_party(int(party_id), walkin_name)
    except Exception as e:
        QMessageBox.warning(parent, "Print balance", str(e))
        return
    _open(parent, data)
