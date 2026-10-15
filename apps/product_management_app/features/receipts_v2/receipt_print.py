# apps/product_management_app/features/receipts_v2/receipt_print.py
# -*- coding: utf-8 -*-
"""
Print controller (raw ESC/POS for 80mm thermal printers, e.g. Xprinter XP-80C).

Why raw: the Windows/QPrinter "page" model fights the Xprinter driver's fixed
paper length (e.g. 80x210mm), which caused wrapping and receipts spilling onto a
second sheet across machines. This path sends the rendered text straight to the
printer as bytes and lets the printer cut itself - there is no Windows page to
overflow, so it behaves identically on every PC regardless of driver settings.

Order of attempts for each print:
  1. RAW ESC/POS via win32print to the default (or named) printer   [preferred]
  2. If pywin32 / raw send fails -> fall back to the old QPrinter path
  3. If there is no printer at all -> offer to save a PDF
"""

from __future__ import annotations

# ---- optional Windows raw printing -----------------------------------------
try:
    import win32print  # from pywin32
    _HAVE_WIN32 = True
except Exception:
    _HAVE_WIN32 = False

from PySide2.QtGui import QTextDocument, QFont, QPageSize
from PySide2.QtPrintSupport import (
    QPrinter, QPrintDialog, QPrinterInfo,
)
from PySide2.QtWidgets import QMessageBox, QFileDialog
from PySide2.QtCore import QSizeF

from .receipt_models import (
    MODE_FULL, MODE_DELIVERY,
    SALE, PURCHASE, PAYMENT, SALE_RETURN, PURCHASE_RETURN,
    BOLD_MARKER,
)
from .receipt_render import render_receipt
from . import receipt_data as _data
from .shop import get_shop


PAPER_WIDTH_MM = 80.0
FONT_PT = 9  # used only by the QPrinter fallback; raw path uses the printer's font

# ESC/POS control codes
_ESC = b"\x1b"
_GS = b"\x1d"
_INIT = _ESC + b"@"                 # initialise printer
_ALIGN_LEFT = _ESC + b"a" + b"\x00"
_FEED_3 = b"\n\n\n"                 # feed a few lines before the cut
_CUT = _GS + b"V" + b"\x42" + b"\x00"   # GS V 66 0 -> partial cut with feed
_BOLD_ON = _ESC + b"E" + b"\x01"    # ESC E 1 -> bold on
_BOLD_OFF = _ESC + b"E" + b"\x00"   # ESC E 0 -> bold off


def _encode(text: str) -> bytes:
    """Encode receipt text for the printer. CP437 is the classic ESC/POS page.
    BOLD_MARKER-wrapped segments become real ESC/POS bold on/off codes."""
    body = text.replace("\r\n", "\n").replace("\r", "\n")

    out = _INIT + _ALIGN_LEFT
    segments = body.split(BOLD_MARKER)
    # Even-indexed segments are normal text; odd-indexed ones are bold -
    # str.split alternates this way whenever the marker appears in pairs.
    for i, seg in enumerate(segments):
        try:
            seg_bytes = seg.encode("cp437", errors="replace")
        except Exception:
            seg_bytes = seg.encode("ascii", errors="replace")
        if i % 2 == 1:
            out += _BOLD_ON + seg_bytes + _BOLD_OFF
        else:
            out += seg_bytes
    return out + _FEED_3 + _CUT


def _raw_print(printer_name: str, text: str) -> bool:
    """Send raw ESC/POS bytes to a Windows printer by name. True on success."""
    if not (_HAVE_WIN32 and printer_name):
        return False
    payload = _encode(text)
    h = win32print.OpenPrinter(printer_name)
    try:
        # ("doc name", None, "RAW") tells the spooler not to touch the bytes
        win32print.StartDocPrinter(h, 1, ("Rafawa Receipt", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, payload)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    return True


def _default_printer_name() -> str:
    if not _HAVE_WIN32:
        return ""
    try:
        return win32print.GetDefaultPrinter() or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# QPrinter fallback (only used if the raw path is unavailable / fails)
# ---------------------------------------------------------------------------

def _build_document(text: str) -> QTextDocument:
    doc = QTextDocument()
    font = QFont("Courier New")
    font.setPointSize(FONT_PT)
    font.setStyleHint(QFont.Monospace)
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    # This path has no bold support - strip the markers rather than
    # print the literal sentinel character.
    doc.setPlainText(text.replace(BOLD_MARKER, ""))
    return doc


def _apply_page(printer: QPrinter, line_count: int) -> None:
    mm_per_line = FONT_PT * 0.46 + 0.9
    height_mm = 12.0 + line_count * mm_per_line
    height_mm = max(60.0, min(height_mm, 2000.0))
    page = QPageSize(QSizeF(PAPER_WIDTH_MM, height_mm),
                     QPageSize.Millimeter, "Receipt80", QPageSize.ExactMatch)
    printer.setPageSize(page)
    printer.setPageMargins(2, 3, 2, 3, QPrinter.Millimeter)
    printer.setFullPage(False)


def _print_text_to(printer: QPrinter, text: str) -> None:
    doc = _build_document(text)
    try:
        doc.setPageSize(QSizeF(printer.pageRect(QPrinter.DevicePixel).size()))
    except Exception:
        try:
            doc.setPageSize(QSizeF(printer.pageRect().size()))
        except Exception:
            pass
    doc.print_(printer)


def _qprinter_dialog(parent, text: str, window_title: str) -> bool:
    """Old path: native dialog + QPrinter. Returns True if it handled printing."""
    line_count = max(1, text.count("\n") + 1)
    try:
        available = QPrinterInfo.availablePrinters()
    except Exception:
        available = []
    if not available:
        return False
    printer = QPrinter(QPrinter.HighResolution)
    _apply_page(printer, line_count)
    dlg = QPrintDialog(printer, parent)
    dlg.setWindowTitle(window_title)
    if dlg.exec_() == QPrintDialog.Accepted:
        _apply_page(printer, line_count)
        _print_text_to(printer, text)
    return True


def _offer_pdf(parent, text: str) -> None:
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
    printer = QPrinter(QPrinter.HighResolution)
    _apply_page(printer, max(1, text.count("\n") + 1))
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    _print_text_to(printer, text)
    QMessageBox.information(parent, "Saved", f"Receipt saved to:\n{path}")


# ---------------------------------------------------------------------------
# public print entry used by everything below
# ---------------------------------------------------------------------------

def print_text(parent, text: str, *, window_title: str = "Print Receipt") -> None:
    """
    Print one receipt. Tries raw ESC/POS first (no Windows page -> no second
    sheet / no wrapping), then the QPrinter dialog, then PDF.
    """
    # 1) preferred: raw ESC/POS to the default printer
    name = _default_printer_name()
    if name:
        try:
            if _raw_print(name, text):
                return
        except Exception as e:
            try:
                QMessageBox.warning(
                    parent, "Direct print failed",
                    f"Raw print failed ({e}). Falling back to the print dialog.",
                )
            except Exception:
                pass

    # 2) fallback: native QPrinter dialog
    try:
        if _qprinter_dialog(parent, text, window_title):
            return
    except Exception:
        pass

    # 3) last resort: no printer -> PDF
    _offer_pdf(parent, text)


# ---------------------------------------------------------------------------
# document entry points (unchanged API/behaviour)
# ---------------------------------------------------------------------------

def print_receipt(parent, doc_type: str, entity_id: int,
                  mode: str = MODE_FULL, *, shop=None) -> None:
    shop = shop or get_shop()
    if (doc_type or "").strip().lower() == PAYMENT:
        # a payment on a sale/purchase gets the small payment receipt; anything else keeps the general one
        try:
            from .payment_slip import build_payment_slip, render_payment_slip
            d = build_payment_slip(entity_id)
            print_text(parent, render_payment_slip(d, shop), window_title=f"Payment Receipt - {d['receipt_no']}")
            return
        except ValueError:
            pass
    doc = _data.build(doc_type, entity_id, shop=shop)
    text = render_receipt(doc, mode)
    print_text(parent, text, window_title=f"{doc.title} - {doc.receipt_no}")


def print_combined_delivery_note(parent, purchase_ids, *, shop=None) -> None:
    """
    One delivery note covering every supplier in a Quick Purchase batch -
    see receipt_data.build_combined_delivery_note for the full reasoning.
    Goes through the exact same print_text pipeline as every other
    document (raw ESC/POS first, then QPrinter, then PDF).
    """
    text = _data.build_combined_delivery_note(purchase_ids, shop or get_shop())
    print_text(parent, text, window_title="Delivery Note (multiple suppliers)")


def print_quick_purchase_supplier_receipt(parent, purchase_id: int, driver_name: str,
                                          *, shop=None) -> None:
    """
    The real Sales/Quick Purchase supplier document - a "Purchase
    Request" for collection, addressed to the driver picking up the
    goods. See receipt_data.build_quick_purchase_supplier_note for the
    full reasoning.
    """
    text = _data.build_quick_purchase_supplier_note(purchase_id, driver_name, shop or get_shop())
    print_text(parent, text, window_title=f"Purchase Request - PUR-{purchase_id}")


def print_delivery_and_full(parent, doc_type: str, entity_id: int, *, shop=None) -> None:
    shop = shop or get_shop()
    doc = _data.build(doc_type, entity_id, shop=shop)
    print_text(parent, render_receipt(doc, MODE_DELIVERY),
               window_title=f"Delivery Note - {doc.receipt_no}")
    print_text(parent, render_receipt(doc, MODE_FULL),
               window_title=f"{doc.title} - {doc.receipt_no}")


def print_full_then_optional_delivery(parent, doc_type: str, entity_id: int,
                                      *, shop=None, ask: bool = True) -> None:
    shop = shop or get_shop()
    doc = _data.build(doc_type, entity_id, shop=shop)

    print_text(parent, render_receipt(doc, MODE_FULL),
               window_title=f"{doc.title} - {doc.receipt_no}")

    can_deliver = doc_type in (SALE, PURCHASE, SALE_RETURN, PURCHASE_RETURN)
    if ask and can_deliver:
        resp = QMessageBox.question(
            parent, "Delivery Note",
            "Print a delivery note (products only) as well?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            print_text(parent, render_receipt(doc, MODE_DELIVERY),
                       window_title=f"Delivery Note - {doc.receipt_no}")


# Kept for compatibility with any code importing it; raw path needs no preview.
def preview_and_print(parent, text: str, *, window_title: str = "Receipt Preview") -> None:
    print_text(parent, text, window_title=window_title)