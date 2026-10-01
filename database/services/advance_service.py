# database/services/advance_service.py

from datetime import datetime, time
from typing import Dict
from sqlalchemy import func

from database.session import get_session
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment
from database.models.user import User

class AdvanceService:
    """
    Rafawa Advance Policy
    Advances represent monetary prefunding only.
    The advance table records only the financial exposure.
    """

    # -------------------------------------------------
    # RECORD ADVANCE IN (🛡️ PURIFIED: Absolute Lane Isolation)
    # -------------------------------------------------
    @staticmethod
    def record_advance_in(
        party_id: int,
        amount: float,
        created_by_user_id: int,
        ledger: str = "crops",
        notes: str = ""
    ) -> int:

        if party_id <= 0:
            raise ValueError("party_id required")
        if amount <= 0:
            raise ValueError("amount must be > 0")

        ledger = (ledger or "crops").lower()
        amount_to_return = float(amount)

        with get_session() as s:
            # 🚀 FIXED: Cross-lane interception blocks completely destroyed.
            # Advance IN now records cleanly as a pristine, independent liability row.
            adv = Advance(
                party_id=party_id,
                direction="received",
                advance_type="money",
                ledger=ledger,
                amount=amount_to_return,
                notes=str(notes or "Counter-advance deposit collection"),
                status="completed",
                created_by_user_id=created_by_user_id,
                created_at=datetime.now()
            )
            s.add(adv)
            s.flush()
            s.commit()

            return int(adv.id)

    # ---------------------------------------------------
    # RECORD ADVANCE OUT (🛡️ PURIFIED: Absolute Lane Isolation)
    # -------------------------------------------------
    @staticmethod
    def record_advance_out(
        party_id: int,
        amount: float,
        created_by_user_id: int,
        ledger: str = "crops",
        notes: str = ""
    ) -> int:

        if party_id <= 0:
            raise ValueError("party_id required")
        if amount <= 0:
            raise ValueError("amount must be > 0")

        ledger = (ledger or "crops").lower()
        amount_to_collect = float(amount)

        with get_session() as s:
            # 🚀 FIXED: Cross-lane interception blocks completely destroyed.
            # Advance OUT now records cleanly as a pristine, independent asset row.
            adv = Advance(
                party_id=party_id,
                direction="given",
                advance_type="money",
                ledger=ledger,
                amount=amount_to_collect,
                notes=str(notes or "Counter-advance payout collection"),
                status="completed",
                created_by_user_id=created_by_user_id,
                created_at=datetime.now()
            )
            s.add(adv)
            s.flush()
            s.commit()

            return int(adv.id)

    # -------------------------------------------------
    # SETTLE ADVANCE PAYMENT (Kept intact for same-day single-row voids)
    # -------------------------------------------------
    @staticmethod
    def settle_advance_payment(
        advance_id: int,
        amount: float,
        method: str,
        created_by_user_id: int
    ):
        from database.models.payment import Payment

        if amount <= 0:
            raise ValueError("amount must be > 0")

        with get_session() as s:
            adv = s.get(Advance, advance_id)
            if not adv:
                raise ValueError("Advance not found")

            # Settlement ALWAYS records a payment against the advance, so the
            # advance and its settlement stay as permanent history. (Previously a
            # same-day full settlement silently DELETED the advance row, which is
            # why the advance and its payment vanished from the profile.)
            remaining = AdvanceService._get_advance_remaining(s, advance_id)
            if amount > remaining + 0.001:
                raise ValueError("Amount exceeds remaining advance")

            direction = "in" if adv.direction == "given" else "out"

            pay = Payment(
                party_id=adv.party_id,
                entity_type="advance",
                entity_id=advance_id,
                direction=direction,
                amount=float(amount),
                method=str(method),
                notes="Advance settlement",
                created_by_user_id=created_by_user_id,
                created_at=datetime.now()
            )
            s.add(pay)
            s.commit()
            return pay.id

    @staticmethod
    def _get_advance_remaining(s, advance_id: int):
        advance = s.get(Advance, advance_id)
        paid = (
            s.query(func.coalesce(func.sum(Payment.amount), 0))
            .filter(Payment.entity_type == "advance", Payment.entity_id == advance_id)
            .filter(Payment.status != "voided")
            .scalar()
        )
        return float(advance.amount) - float(paid)

    @staticmethod
    def get_fifo_advances(party_id: int, direction: str):
        with get_session() as s:
            advances = (
                s.query(Advance)
                .filter(Advance.party_id == party_id, Advance.advance_type == "money", Advance.status == "completed", Advance.direction == direction)
                .order_by(Advance.created_at.asc())
                .all()
            )
            result = []
            for adv in advances:
                allocated = (s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0)).filter(AdvanceAllocation.advance_id == adv.id).scalar())
                paid = (s.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.entity_type == "advance", Payment.entity_id == adv.id, Payment.status != "voided").scalar())
                remaining = float(adv.amount) - float(paid) - float(allocated)
                if remaining > 0:
                    result.append({"advance_id": adv.id, "remaining": remaining, "direction": adv.direction})
            return result

    @staticmethod
    def allocate_fifo(party_id: int, entity_type: str, entity_id: int, transaction_total: float, created_by_user_id: int):
        remaining_tx = float(transaction_total)
        allocations = []
        with get_session() as s:
            direction = "given" if entity_type == "purchase" else "received"
            advances = (
                s.query(Advance)
                .filter(Advance.party_id == party_id, Advance.advance_type == "money", Advance.status == "completed", Advance.direction == direction)
                .order_by(Advance.created_at.asc())
                .all()
            )
            for adv in advances:
                allocated = (s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0)).filter(AdvanceAllocation.advance_id == adv.id).scalar())
                paid = (s.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.entity_type == "advance", Payment.entity_id == adv.id, Payment.status != "voided").scalar())
                remaining_adv = float(adv.amount) - float(paid) - float(allocated)

                if remaining_adv <= 0:
                    continue

                use_amount = min(remaining_adv, remaining_tx)
                alloc = AdvanceAllocation(advance_id=adv.id, entity_type=entity_type, entity_id=entity_id, amount=use_amount, created_by_user_id=created_by_user_id)
                s.add(alloc)
                allocations.append({"advance_id": adv.id, "amount": use_amount})
                remaining_tx -= use_amount
                if remaining_tx <= 0:
                    break
            s.commit()
        return {"allocated": allocations, "remaining_transaction": remaining_tx}

    @staticmethod
    def get_party_advance_balance(party_id: int) -> Dict:
        if party_id <= 0:
            return {"advance_in": 0.0, "advance_out": 0.0, "balance": 0.0}
        with get_session() as s:
            advance_in = (s.query(func.coalesce(func.sum(Advance.amount), 0)).filter(Advance.party_id == party_id, Advance.direction == "received", Advance.advance_type == "money", Advance.status == "completed").scalar())
            advance_out = (s.query(func.coalesce(func.sum(Advance.amount), 0)).filter(Advance.party_id == party_id, Advance.direction == "given", Advance.advance_type == "money", Advance.status == "completed").scalar())
        balance = float(advance_in) - float(advance_out)
        return {"advance_in": float(advance_in), "advance_out": float(advance_out), "balance": float(balance)}

    @staticmethod
    def list_party_advances(party_id: int, created_by_user_id: int, limit: int = 500):
        with get_session() as s:
            rows = (s.query(Advance).filter(Advance.party_id == party_id, Advance.created_by_user_id == created_by_user_id, Advance.status == "completed").order_by(Advance.created_at.desc()).limit(limit).all())
        return [r.to_dict() for r in rows]

    # -------------------------------------------------
    # FULL PARTY ADVANCE HISTORY (any operator, non-draft)
    # -------------------------------------------------
    @staticmethod
    def list_all_party_advances(party_id: int, limit: int = 500):
        """
        Every non-draft advance (given or received) for a party, newest first,
        regardless of which user recorded it. Returns plain dicts ready for the
        Advances tab, with the operator username resolved.
        """
        out = []
        with get_session() as s:
            rows = (
                s.query(Advance)
                .filter(Advance.party_id == int(party_id),
                        Advance.status != "draft")
                .order_by(Advance.created_at.desc())
                .limit(limit)
                .all()
            )
            for r in rows:
                operator = ""
                if r.created_by_user_id:
                    u = s.query(User).get(r.created_by_user_id)
                    operator = u.username if u else ""
                out.append({
                    "id": r.id,
                    "created_at": r.created_at,
                    "direction": r.direction,
                    "advance_type": r.advance_type,
                    "amount": float(r.amount or 0),
                    "qty_bags": int(r.qty_bags or 0),
                    "total_weight_kg": float(r.total_weight_kg or 0),
                    "ledger": r.ledger or "",
                    "status": r.status,
                    "operator": operator,
                    "notes": r.notes or "",
                })
        return out

    # -------------------------------------------------
    # VOID AN ADVANCE (mistake correction, keeps history)
    # -------------------------------------------------
    @staticmethod
    def get_advance_settlement_impact(advance_id: int) -> Dict:
        """
        What voiding THIS advance would actually undo - for the UI to show
        a specific, honest warning before a cascading void, naming exactly
        which purchases/sales would be put back to unpaid, rather than a
        vague "this will affect other records" message.
        """
        advance_id = int(advance_id or 0)
        with get_session() as s:
            adv = s.get(Advance, advance_id)
            if not adv:
                raise ValueError("Advance not found")

            payments = (
                s.query(Payment)
                .filter(Payment.entity_type == "advance", Payment.entity_id == advance_id)
                .filter(Payment.status != "voided")
                .all()
            )
            allocations = (
                s.query(AdvanceAllocation)
                .filter(AdvanceAllocation.advance_id == advance_id)
                .all()
            )

            paid_total = sum(float(p.amount or 0) for p in payments)
            allocated_total = sum(float(a.amount or 0) for a in allocations)
            settled_total = paid_total + allocated_total

            affected_docs = [
                {"entity_type": a.entity_type, "entity_id": a.entity_id, "amount": float(a.amount or 0)}
                for a in allocations
            ]

        return {
            "advance_id": advance_id,
            "advance_amount": float(adv.amount or 0),
            "paid_total": paid_total,
            "allocated_total": allocated_total,
            "settled_total": settled_total,
            "remaining": max(float(adv.amount or 0) - settled_total, 0.0),
            "fully_settled": settled_total >= float(adv.amount or 0) - 0.01,
            "payment_count": len(payments),
            "affected_documents": affected_docs,
        }

    @staticmethod
    def void_advance(advance_id: int, created_by_user_id: int, reason: str = "",
                     cascade: bool = False):
        """
        Void an advance entered by mistake. It stays visible in history
        (status 'voided') but drops out of all balances.

        Three cases, matching how much of it has already been used:

          Fully settled (100% paid/allocated) - ALWAYS refused, no matter
          what. The money has genuinely, entirely moved or been applied
          elsewhere; there is nothing left here to safely undo.

          Unpaid (0% settled) - a simple void, same as before.

          Partially settled (some %, less than 100%) - requires cascade=
          True, explicitly. Voiding a partially-settled advance also
          reverses everything built on top of it: every non-voided
          Payment against it gets voided (same append-only convention as
          PaymentService.void_payment - never deleted, never edited), and
          every AdvanceAllocation against it gets removed (this table has
          no status/void mechanism of its own to preserve - once the
          advance itself is gone, a record of "how much of it was applied
          where" has nothing left to point at). Any purchase or sale an
          allocation had covered goes back to genuinely unpaid as a real,
          visible consequence - not a side effect to discover later. The
          caller should always show the operator exactly what
          get_advance_settlement_impact() reports before setting
          cascade=True; this method does not warn on its own.
        """
        with get_session() as s:
            adv = s.get(Advance, int(advance_id))
            if not adv:
                raise ValueError("Advance not found")
            if adv.status == "voided":
                return adv.id

            payments = (
                s.query(Payment)
                .filter(Payment.entity_type == "advance",
                        Payment.entity_id == int(advance_id))
                .filter(Payment.status != "voided")
                .all()
            )
            allocations = (
                s.query(AdvanceAllocation)
                .filter(AdvanceAllocation.advance_id == int(advance_id))
                .all()
            )
            paid = sum(float(p.amount or 0) for p in payments)
            allocated = sum(float(a.amount or 0) for a in allocations)
            settled = paid + allocated

            if settled >= float(adv.amount or 0) - 0.01:
                raise ValueError(
                    "Cannot void this advance - it is fully settled. The "
                    "money has genuinely, entirely moved or been applied "
                    "elsewhere; there is nothing left here to safely undo."
                )

            if settled > 0.005 and not cascade:
                raise ValueError(
                    f"This advance is partially settled ({settled:,.2f} of "
                    f"{float(adv.amount or 0):,.2f}). Voiding it will also "
                    f"reverse those settlements, putting whatever they "
                    f"covered back to unpaid. Call get_advance_settlement_"
                    f"impact() to see exactly what's affected, then confirm "
                    f"with cascade=True."
                )

            if settled > 0.005 and cascade:
                for p in payments:
                    p.status = "voided"
                    p.voided_at = datetime.now()
                    p.voided_by_user_id = int(created_by_user_id or 0)
                    p.void_reason = (f"Cascaded from voiding advance #{advance_id}"
                                     if not reason else f"{reason} (cascaded)")[:255]
                for a in allocations:
                    s.delete(a)

            adv.status = "voided"
            if reason:
                adv.notes = (adv.notes or "") + f" | [Voided: {reason}]"
            s.commit()
            return adv.id