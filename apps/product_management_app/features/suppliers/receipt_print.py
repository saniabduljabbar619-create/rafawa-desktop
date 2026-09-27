# apps/product_management_app/features/receipts_v2/receipt_print.py
# -*- coding: utf-8 -*-
"""
Print controller (PySide2).

Key behaviour change from the old engine: NO AUTO-PRINT.
Nothing is ever sent to the OS spooler in the background. Every receipt goes
through a Print Preview window; a job is only spooled when the user clicks the
Print button inside that preview, against a printer they can see. That removes
the old failure mode where receipts silently queued while the printer was
unplugged and then all dumped at once when it reconnected.

If the machine has no printers at all, we skip the preview and offer to save a
PDF instead - again, never queuing anything.

Tuning knobs (match receipt_render.WIDTH):
    PAPER_WIDTH_MM  - physical paper width (80mm roll)
    FONT_PT         - monospace point size; lower it if lines wrap/clip
"""

from __future__ import annotations

from typing import Optional

from PySide2.QtGui import QTextDocument, QFont
from PySide2.QtPrintSupport import QPrinter, QPrintPreviewDialog, QPrinterInfo
from PySide2.QtWidgets import QMessageBox, QFileDialog
from PySide2.QtCore import QSizeF

from .receipt_models import MODE_FULL, MODE_DELIVERY
from .receipt_render import render_receipt
from . import receipt_data as _data
from .shop import get_shop


PAPER_WIDTH_MM = 80.0
FONT_PT = 9


# ---------------------------------------------------------------------------
# document / printer setup
# ---------------------------------------------------------------------------

def _build_document(text: str) -> QTextDocument:
    doc = QTextDocument()
    font = QFont("Courier New")
    font.setPointSize(FONT_PT)
    font.setStyleHint(QFont.Monospace)
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    doc.setPlainText(text)
    return doc


def _configure_printer(printer: QPrinter, line_count: int) -> None:
    # Estimate a continuous-roll page height from the line count so the
    # preview shows a receipt-shaped page instead of a full A4 sheet.
    mm_per_line = FONT_PT * 0.46 + 0.9
    height_mm = 12.0 + line_count * mm_per_line
    height_mm = max(60.0, min(height_mm, 2000.0))

    printer.setFullPage(True)
    try:
        printer.setPageMargins(3, 4, 3, 4, QPrinter.Millimeter)
    except Exception:
        pass
    try:
        printer.setPaperSize(QSizeF(PAPER_WIDTH_MM, height_mm), QPrinter.Millimeter)
    except Exception:
        try:
            printer.setPaperSize(QSizeF(PAPER_WIDTH_MM, height_mm))
        except Exception:
            pass


def _print_text_to(printer: QPrinter, text: str) -> None:
    doc = _build_document(text)
    try:
        doc.setPageSize(QSizeF(printer.pageRect().size()))
    except Exception:
        pass
    doc.print_(printer)


# ---------------------------------------------------------------------------
# public: preview + print
# ---------------------------------------------------------------------------

def preview_and_print(parent, text: str, *, window_title: str = "Receipt Preview") -> None:
    """Show a print preview. Nothing prints until the user clicks Print."""
    line_count = max(1, text.count("\n") + 1)

    try:
        available = QPrinterInfo.availablePrinters()
    except Exception:
        available = []

    printer = QPrinter(QPrinter.HighResolution)
    _configure_printer(printer, line_count)

    if not available:
        _offer_pdf(parent, text, printer)
        return

    dlg = QPrintPreviewDialog(printer, parent)
    dlg.setWindowTitle(window_title)
    dlg.paintRequested.connect(lambda p: _print_text_to(p, text))
    dlg.exec_()


def _offer_pdf(parent, text: str, printer: QPrinter) -> None:
    QMessageBox.information(
        parent, "No printer found",
        "No printer is available on this machine.\n"
        "You can save this receipt as a PDF instead.",
    )
    path, _ = QFileDialog.getSaveFileName(
        parent, "Save receipt as PDF", "receipt.pdf", "PDF files (*.pdf)"
    )
    if not path:
        return
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    _print_text_to(printer, text)
    QMessageBox.information(parent, "Saved", f"Receipt saved to:\n{path}")


# ---------------------------------------------------------------------------
# public: one-call entry points used by the app
# ---------------------------------------------------------------------------

def print_receipt(parent, doc_type: str, entity_id: int,
                  mode: str = MODE_FULL, *, shop=None) -> None:
    """
    Build a receipt from the DB and open its preview.

    doc_type : "sale" | "purchase" | "advance" | "payment"
               | "sale_return" | "purchase_return"
    mode     : MODE_FULL      -> amounts + payments   (Receipt type 2)
               MODE_DELIVERY   -> products only        (Receipt type 1)
    """
    doc = _data.build(doc_type, entity_id, shop=shop or get_shop())
    text = render_receipt(doc, mode)
    preview_and_print(parent, text, window_title=f"{doc.title} - {doc.receipt_no}")


def print_delivery_and_full(parent, doc_type: str, entity_id: int, *, shop=None) -> None:
    """
    Convenience for flows that want BOTH slips for one transaction:
    a products-only delivery note, then the full financial receipt.
    Two previews open in sequence (delivery first).
    """
    shop = shop or get_shop()
    doc = _data.build(doc_type, entity_id, shop=shop)
    preview_and_print(parent, render_receipt(doc, MODE_DELIVERY),
                      window_title=f"Delivery Note - {doc.receipt_no}")
    preview_and_print(parent, render_receipt(doc, MODE_FULL),
                      window_title=f"{doc.title} - {doc.receipt_no}")