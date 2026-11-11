# apps/product_management_app/features/receipts_v2/closing_slip_dialog.py
# -*- coding: utf-8 -*-
"""
Dialog to preview and print a Customer Closing Statement slip on an 80mm thermal printer.
Supports Hausa (default) and English.
"""
from __future__ import annotations

import json
import logging

from PySide2.QtGui import QFont
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from shared.paths import data_dir
from .closing_slip import DEFAULT_LANG, LANGUAGES, render_closing_slip
from .receipt_models import BOLD_MARKER
from .receipt_print import print_text
from .shop import get_shop

log = logging.getLogger("rafawa.closing_slip")
_PREFS = "print_prefs.json"


def _load_lang() -> str:
    try:
        lang = json.loads((data_dir() / _PREFS).read_text(encoding="utf-8")).get("closing_lang", DEFAULT_LANG)
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
        prefs["closing_lang"] = lang
        path.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        log.exception("Could not remember closing slip language")


class ClosingSlipDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.data = data
        self.setWindowTitle(f"Customer Closing Slip - {data.get('party_name') or ''}")
        self.setMinimumWidth(500)
        self.setMinimumHeight(600)
        lay = QVBoxLayout(self)

        title = QLabel(
            f"<b>{data.get('party_name') or 'Customer'}</b> &nbsp;&bull;&nbsp; "
            f"Closing #{data.get('closing_no') or 'DRAFT'}"
        )
        title.setStyleSheet("font-size: 15px; margin-bottom: 4px;")
        lay.addWidget(title)

        form = QFormLayout()
        self.combo_lang = QComboBox()
        for code, label in LANGUAGES.items():
            self.combo_lang.addItem(label, code)
        cur_lang = _load_lang()
        idx = self.combo_lang.findData(cur_lang)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(self._refresh_preview)
        form.addRow("Language / Yare:", self.combo_lang)

        self.chk_items = QCheckBox("Include itemized activity list (Sayayya, Tallace, Advances)")
        self.chk_items.setChecked(True)
        self.chk_items.toggled.connect(self._refresh_preview)
        form.addRow("", self.chk_items)
        lay.addLayout(form)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 10))
        self.preview.setStyleSheet(
            "background-color: #fafafa; border: 1px solid #d1d5db; border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(self.preview, 1)

        btn_row = QHBoxLayout()
        btn_print = QPushButton("Print Slip (80mm)")
        btn_print.setStyleSheet(
            "background-color: #1e3a8a; color: white; font-weight: bold; padding: 7px 16px; border-radius: 4px;"
        )
        btn_print.clicked.connect(self._do_print)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(btn_print)
        lay.addLayout(btn_row)

        self._refresh_preview()

    def _selected_lang(self) -> str:
        return self.combo_lang.currentData() or DEFAULT_LANG

    def _rendered_slip(self) -> str:
        lang = self._selected_lang()
        shop = get_shop()
        include_items = self.chk_items.isChecked()
        return render_closing_slip(self.data, shop=shop, lang=lang, include_items=include_items)

    def _refresh_preview(self):
        text = self._rendered_slip().replace(BOLD_MARKER, "")
        self.preview.setPlainText(text)

    def _do_print(self):
        lang = self._selected_lang()
        _save_lang(lang)
        raw = self._rendered_slip()
        ok, err = print_text(raw)
        if ok:
            QMessageBox.information(self, "Printed", "Closing statement printed successfully.")
            self.accept()
        else:
            QMessageBox.critical(self, "Print Error", f"Failed to send to printer:\n{err}")


def open_closing_slip(parent, data: dict):
    dlg = ClosingSlipDialog(data, parent=parent)
    dlg.exec_()
