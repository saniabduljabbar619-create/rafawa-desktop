# apps/product_management_app/features/receipts_v2/demo_preview.py
# -*- coding: utf-8 -*-
"""
Standalone preview / proof - NO database required.

    python -m apps.product_management_app.features.receipts_v2.demo_preview

Writes two TRUE 80mm PDFs to your Desktop and opens them, so you can judge the
real thermal-width layout even without a thermal printer plugged in:
    receipt_type1_delivery.pdf   (products only)
    receipt_type2_full.pdf       (amounts + payment)

It then also opens the interactive Print Preview. NOTE: on a dev machine whose
default printer is A4 (e.g. "Microsoft Print to PDF"), that preview shows the
receipt on an A4 sheet - that's the driver, not the receipt. On the real 80mm
thermal printer the page is 80mm. The PDFs above always show the true size.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

from PySide2.QtWidgets import QApplication
from PySide2.QtPrintSupport import QPrinter

from .receipt_models import (
    ReceiptDoc, ReceiptLine, PaymentLine, PartyInfo,
    SALE, MODE_DELIVERY, MODE_FULL,
)
from .shop import get_shop
from .receipt_render import render_receipt
from .receipt_print import preview_and_print, _apply_page, _print_text_to


def _sample_sale() -> ReceiptDoc:
    return ReceiptDoc(
        doc_type=SALE, title="Sales Receipt", receipt_no="SAL-00012",
        shop=get_shop(),
        party=PartyInfo(name="Musa Ibrahim", phone="08030001122", kind="customer"),
        when=datetime.now(), served_by="Abdoull", ledger="crops",
        lines=[
            ReceiptLine(name="Masara (White Maize)", bags=10, kg=900, rate=500, amount=450000),
            ReceiptLine(name="NPK Fertilizer", bags=5, is_fertilizer=True, rate=12000, amount=60000),
        ],
        payments=[PaymentLine(amount=300000, method="cash")],
        subtotal=510000, discount=10000, total=500000, paid=300000,
    )


def _save_pdf(text: str, path: str) -> str:
    """Write an exact-80mm PDF. PDF honours the custom page on any machine."""
    line_count = max(1, text.count("\n") + 1)
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    _apply_page(printer, line_count)
    _print_text_to(printer, text)
    return path


def _target_folder() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else tempfile.gettempdir()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    sale = _sample_sale()
    folder = _target_folder()

    p1 = _save_pdf(render_receipt(sale, MODE_DELIVERY),
                   os.path.join(folder, "receipt_type1_delivery.pdf"))
    p2 = _save_pdf(render_receipt(sale, MODE_FULL),
                   os.path.join(folder, "receipt_type2_full.pdf"))
    print("Saved 80mm PDFs (this is the true thermal size):")
    print("  ", p1)
    print("  ", p2)
    for p in (p1, p2):
        try:
            os.startfile(p)          # Windows: open in default PDF viewer
        except Exception:
            pass

    # Interactive preview too (A4-looking on a dev machine - see module note).
    preview_and_print(None, render_receipt(sale, MODE_FULL),
                      window_title="TYPE 2 - Full Receipt (interactive)")


if __name__ == "__main__":
    main()