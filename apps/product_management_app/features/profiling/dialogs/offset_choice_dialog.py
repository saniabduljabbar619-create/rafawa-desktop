# apps/product_management_app/features/profiling/dialogs/offset_choice_dialog.py
"""
After a purchase is recorded from a party who still OWES US for unpaid sales, offer to set the
purchase against those sales instead of paying cash. Nothing is ever applied automatically.
"""
from __future__ import annotations

from PySide2.QtWidgets import QMessageBox

from database.services.document_balance import document_balances
from database.services.offset_service import OffsetService
from database.session import get_session


def purchase_balance(purchase_id: int) -> float:
    with get_session() as s:
        b = document_balances(s, "purchase", [int(purchase_id)]).get(int(purchase_id))
        return b.balance if b else 0.0


def prompt_and_apply_offset(parent, *, party_id: int, purchase_id: int, party_name: str = "") -> float:
    """Returns what is still owed on the purchase afterwards (so the caller knows whether to take payment)."""
    try:
        pv = OffsetService.preview(int(party_id), purchase_ids=[int(purchase_id)])
    except Exception:
        return purchase_balance(purchase_id)
    if pv["offsettable"] <= 0:
        return purchase_balance(purchase_id)

    name = party_name or "This party"
    left_over = round(pv["we_owe"] - pv["offsettable"], 2)
    msg = (f"{name} still owes us {pv['they_owe']:,.2f} on unpaid sales.\n\n"
           f"This purchase is worth {pv['we_owe']:,.2f}.\n"
           f"Use it to settle {pv['offsettable']:,.2f} of what they owe?\n\n"
           + (f"We would still owe them {left_over:,.2f} on this purchase."
              if left_over > 0 else "That clears this purchase completely.")
           + (f"\nThey would still owe us {pv['they_owe'] - pv['offsettable']:,.2f}."
              if pv["they_owe"] > pv["offsettable"] else ""))
    if QMessageBox.question(parent, "Settle their unpaid sales?", msg,
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
        try:
            OffsetService.apply(int(party_id), purchase_ids=[int(purchase_id)], kind="settlement",
                                note="Unpaid sales settled with goods purchased")
        except Exception as e:
            QMessageBox.warning(parent, "Offset", f"Nothing was offset.\n\n{e}")
    return purchase_balance(purchase_id)
