# database/services/cash_shortage_service.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from database.session import get_session
from database.models.cash_shortage import CashShortage


class CashShortageService:
    """
    A physical cash-handling mistake, corrected after the fact - see
    CashShortage's own docstring for the full reasoning. Deliberately
    never touches any sale/purchase balance or the party summary cards;
    stays visible only through list_open_shortages(), which the payment
    dialogs use to show a dedicated, impossible-to-miss banner.
    """

    @staticmethod
    def record_shortage(party_id: int, reference_type: str, reference_id: int,
                        direction: str, amount: float, created_by_user_id: int,
                        notes: str = "") -> int:
        party_id = int(party_id or 0)
        reference_id = int(reference_id or 0)
        created_by_user_id = int(created_by_user_id or 0)
        amount = float(amount or 0)

        if party_id <= 0:
            raise ValueError("party_id is required.")
        if reference_type not in ("sale", "purchase"):
            raise ValueError("reference_type must be 'sale' or 'purchase'.")
        if reference_id <= 0:
            raise ValueError("reference_id is required.")
        if direction not in ("owed_to_party", "owed_to_company"):
            raise ValueError("direction must be 'owed_to_party' or 'owed_to_company'.")
        if amount <= 0:
            raise ValueError("amount must be greater than zero.")
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        with get_session() as s:
            row = CashShortage(
                party_id=party_id,
                reference_type=reference_type,
                reference_id=reference_id,
                direction=direction,
                amount=amount,
                status="open",
                notes=(notes or "").strip() or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(row)
            s.flush()
            return int(row.id)

    @staticmethod
    def list_open_shortages(party_id: int, reference_type: Optional[str] = None) -> List[Dict]:
        """What the payment-dialog banner shows for this party - every
        still-unsettled shortage/overpayment correction, oldest first.

        reference_type, when given ("sale" or "purchase"), scopes this to
        just that side - the sale payment dialog should only ever show a
        sale-originated shortage, never a purchase one for the same
        party, and vice versa. Left unfiltered, every open shortage for
        the party comes back regardless of where it originated."""
        party_id = int(party_id or 0)
        if party_id <= 0:
            return []

        with get_session() as s:
            q = s.query(CashShortage).filter(
                CashShortage.party_id == party_id, CashShortage.status == "open"
            )
            if reference_type:
                q = q.filter(CashShortage.reference_type == reference_type)
            rows = q.order_by(CashShortage.created_at.asc()).all()
            return [
                {
                    "id": int(r.id),
                    "reference_type": r.reference_type,
                    "reference_id": int(r.reference_id),
                    "reference": f"{'SAL' if r.reference_type == 'sale' else 'PUR'}-{r.reference_id}",
                    "direction": r.direction,
                    "amount": float(r.amount or 0),
                    "notes": r.notes or "",
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    @staticmethod
    def settle_shortage(shortage_id: int, settled_by_user_id: int,
                        settlement_notes: str = "") -> Dict:
        """
        Marks a shortage/overpayment as resolved once real cash actually
        changes hands to close it out - append-only, same as every other
        void/settle action in this app: the original record is never
        edited, just marked settled with who and when.
        """
        shortage_id = int(shortage_id or 0)
        settled_by_user_id = int(settled_by_user_id or 0)
        if shortage_id <= 0:
            raise ValueError("shortage_id is required.")
        if settled_by_user_id <= 0:
            raise ValueError("settled_by_user_id is required.")

        with get_session() as s:
            row = s.get(CashShortage, shortage_id)
            if row is None:
                raise ValueError("Shortage record not found.")
            if row.status == "settled":
                return {"ok": True, "shortage_id": shortage_id, "already_settled": True}

            row.status = "settled"
            row.settled_by_user_id = settled_by_user_id
            row.settled_at = datetime.now()
            if settlement_notes:
                row.settlement_notes = settlement_notes.strip()

        return {"ok": True, "shortage_id": shortage_id}