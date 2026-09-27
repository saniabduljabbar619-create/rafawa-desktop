# apps/product_management_app/features/receipts/receipt_printer.py
from __future__ import annotations

from typing import Optional


def _is_windows() -> bool:
    try:
        import os
        return os.name == "nt"
    except Exception:
        return False


def print_text_receipt_windows(text: str, printer_name: Optional[str] = None) -> None:
    """
    Send RAW text directly to Windows printer spooler.
    Best for thermal/X-printers.
    Requires: pywin32 (win32print)
    """
    if not _is_windows():
        raise RuntimeError("RAW printing is only available on Windows.")

    try:
        import win32print  # type: ignore
    except Exception as e:
        raise RuntimeError(f"pywin32/win32print not available: {e}") from e

    text = (text or "").rstrip() + "\n\n"

    if not printer_name:
        try:
            printer_name = win32print.GetDefaultPrinter()
        except Exception as e:
            raise RuntimeError(f"Could not get default printer: {e}") from e

    if not printer_name:
        raise RuntimeError("No printer_name provided and default printer is empty.")

    try:
        handle = win32print.OpenPrinter(printer_name)
    except Exception as e:
        raise RuntimeError(f"Could not open printer '{printer_name}': {e}") from e

    try:
        win32print.StartDocPrinter(handle, 1, ("Rafawa Receipt", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, text.encode("utf-8", errors="replace"))
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def print_text_receipt_qt(parent, text: str, *, paper_mm_width: float = 80.0) -> None:
    """
    Fallback printing using Qt print dialog, but FORCE receipt paper size
    to stop the 'long sheet' issue on thermal printers.

    Key idea:
    - Compute page height from number of lines (dynamic)
    - Set paper size to 80mm x computed_height_mm
    - Remove margins
    """
    from PySide2.QtGui import QFont, QTextDocument
    from PySide2.QtPrintSupport import QPrinter, QPrintDialog
    from PySide2.QtCore import QSizeF

    # --- Prepare document ---
    txt = (text or "").rstrip() + "\n"
    lines = txt.splitlines()
    line_count = max(1, len(lines))

    doc = QTextDocument()
    doc.setDefaultFont(QFont("Courier New", 9))
    doc.setPlainText(txt)

    # --- Printer + dialog ---
    printer = QPrinter(QPrinter.HighResolution)
    dlg = QPrintDialog(printer, parent)

    if dlg.exec_() != dlg.Accepted:
        raise RuntimeError("Print cancelled by user.")

    # --- FORCE receipt sizing AFTER user picks printer ---
    # Typical thermal: ~3.6mm per line at 9pt
    # Add header/footer buffer.
    mm_per_line = 3.6
    base_mm = 35.0  # header/footer buffer
    height_mm = base_mm + (line_count * mm_per_line)

    # Clamp to sane range (prevents driver weirdness)
    if height_mm < 120.0:
        height_mm = 120.0
    if height_mm > 300.0:
        height_mm = 300.0

    # Use full page & no margins (critical for thermal)
    printer.setFullPage(True)
    try:
        printer.setPageMargins(0, 0, 0, 0, QPrinter.Millimeter)  # type: ignore
    except Exception:
        pass

    # Set custom paper size to stop “infinite feed”
    try:
        printer.setPaperSize(QSizeF(float(paper_mm_width), float(height_mm)), QPrinter.Millimeter)  # type: ignore
    except Exception:
        # older Qt fallback
        try:
            printer.setPaperSize(QSizeF(float(paper_mm_width), float(height_mm)))  # type: ignore
        except Exception:
            pass

    # Tell document to wrap to printer width (prevents scaling surprises)
    try:
        doc.setTextWidth(printer.pageRect().width())
    except Exception:
        pass

    doc.print_(printer)


def print_text_receipt(
    parent,
    text: str,
    printer_name: Optional[str] = None,
    *,
    prefer_raw: bool = True,
    paper_mm_width: float = 80.0,
) -> None:
    last_err: Optional[Exception] = None

    if prefer_raw:
        try:
            print_text_receipt_windows(text=text, printer_name=printer_name)
            return
        except Exception as e:
            last_err = e

    try:
        print_text_receipt_qt(parent, text, paper_mm_width=paper_mm_width)
        return
    except Exception as e:
        if last_err is not None:
            raise RuntimeError(f"RAW print failed: {last_err}\nQt print failed: {e}") from e
        raise
