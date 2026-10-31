# database/services/advance_service.py

from datetime import datetime, time
from typing import Dict
from sqlalchemy import func

from database.session import get_session
from database.services.audit_service import AuditService
from shared.auth.permissions import require_admin
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
            # how much of each advance has been used up (applied to a purchase/sale, or settled by payment)
            ids = [int(r.id) for r in rows]
            applied: Dict[int, float] = {}
            if ids:
                for aid, tot in (s.query(AdvanceAllocation.advance_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                                 .filter(AdvanceAllocation.advance_id.in_(ids))
                                 .group_by(AdvanceAllocation.advance_id)):
                    applied[int(aid)] = applied.get(int(aid), 0.0) + float(tot or 0)
                for aid, tot in (s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                                 .filter(Payment.entity_type == "advance", Payment.entity_id.in_(ids),
                                         Payment.status != "voided")
                                 .group_by(Payment.entity_id)):
                    applied[int(aid)] = applied.get(int(aid), 0.0) + float(tot or 0)
            for r in rows:
                operator = ""
                if r.created_by_user_id:
                    u = s.query(User).get(r.created_by_user_id)
                    operator = u.username if u else ""
                used = applied.get(int(r.id), 0.0)
                out.append({
                    "applied": used,
                    "remaining": max(float(r.amount or 0) - used, 0.0),
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
    @require_admin
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
                docs = sorted({(a.entity_type, int(a.entity_id)) for a in allocations})
                for a in allocations:
                    s.delete(a)
                s.flush()
                for et, eid in docs:
                    AdvanceService._restate_advance_payments(s, et, eid, created_by_user_id,
                                                             f"Advance #{advance_id} voided")

            adv.status = "voided"
            if reason:
                adv.notes = (adv.notes or "") + f" | [Voided: {reason}]"
            AuditService.record(
                "advance.void", "advance", advance_id,
                f"Voided {adv.direction} advance of {float(adv.amount or 0):,.2f}",
                {"reason": reason, "cascade": bool(cascade), "amount": float(adv.amount or 0),
                 "settled": settled, "party_id": adv.party_id}, session=s)
            s.commit()
            return adv.id

    # -------------------------------------------------
    # ADVANCE USED ON A PURCHASE / SALE - and taking it back
    # -------------------------------------------------
    @staticmethod
    def _doc_party_direction(s, entity_type: str, entity_id: int):
        from database.models.purchase import Purchase
        from database.models.sale import Sale
        if entity_type == "purchase":
            doc = s.get(Purchase, int(entity_id))
            return (int(doc.supplier_party_id) if doc else 0), "out"
        doc = s.get(Sale, int(entity_id))
        return (int(doc.buyer_party_id) if doc else 0), "in"

    @staticmethod
    def _advance_payments(s, entity_type: str, entity_id: int):
        """The document's active 'paid from advance' payment rows (history rows - the books use the allocations)."""
        return (s.query(Payment)
                .filter(Payment.entity_type == entity_type, Payment.entity_id == int(entity_id),
                        Payment.method == "advance", Payment.status != "voided")
                .order_by(Payment.id.desc()).all())

    @staticmethod
    def _void_payment_rows(rows, user_id: int, reason: str) -> None:
        for p in rows:
            p.status = "voided"
            p.voided_at = datetime.now()
            p.voided_by_user_id = int(user_id or 0)
            p.void_reason = (reason or "Advance taken back")[:255]

    @staticmethod
    def release_in_session(s, entity_type: str, entity_id: int, user_id: int, reason: str = "",
                           allocation_ids=None) -> Dict:
        """
        Take advance money back off one purchase / sale (inside the caller's session): the document's
        AdvanceAllocation rows are removed (all, or just `allocation_ids`), so the advance is unused again and the
        document owes that amount again; the document's 'paid from advance' history rows are voided and, if some
        advance use remains, restated as one row for what is left. Returns {"released": total, "uses": [...]}.
        Nothing is touched when the document has no advance on it.
        """
        entity_type = "purchase" if entity_type == "purchase" else "sale"
        q = (s.query(AdvanceAllocation)
             .filter(AdvanceAllocation.entity_type == entity_type, AdvanceAllocation.entity_id == int(entity_id)))
        if allocation_ids is not None:
            q = q.filter(AdvanceAllocation.id.in_([int(a) for a in allocation_ids] or [0]))
        rows = q.order_by(AdvanceAllocation.id).all()
        uses = [{"allocation_id": int(a.id), "advance_id": int(a.advance_id), "amount": float(a.amount or 0),
                 "applied_at": a.created_at, "applied_by": a.created_by_user_id} for a in rows]
        released = round(sum(u["amount"] for u in uses), 2)
        if not rows:
            return {"released": 0.0, "uses": []}
        for a in rows:
            s.delete(a)
        s.flush()
        AdvanceService._restate_advance_payments(s, entity_type, int(entity_id), user_id, reason)
        return {"released": released, "uses": uses}

    @staticmethod
    def _restate_advance_payments(s, entity_type: str, entity_id: int, user_id: int, reason: str = "") -> None:
        """Make the document's 'paid from advance' history rows add up to the advance still allocated to it."""
        left = float(s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                     .filter(AdvanceAllocation.entity_type == entity_type,
                             AdvanceAllocation.entity_id == int(entity_id)).scalar() or 0)
        pays = AdvanceService._advance_payments(s, entity_type, entity_id)
        if abs(sum(float(p.amount or 0) for p in pays) - left) < 0.005:
            return
        party_id, direction = AdvanceService._doc_party_direction(s, entity_type, entity_id)
        if pays:
            party_id, direction = int(pays[0].party_id), pays[0].direction
        AdvanceService._void_payment_rows(pays, user_id, reason or "Advance taken back")
        if left > 0.005 and party_id:
            s.add(Payment(entity_type=entity_type, entity_id=int(entity_id), party_id=party_id, direction=direction,
                          amount=left, method="advance", notes="Advance settlement (restated after a revert)",
                          created_by_user_id=int(user_id or 0), created_at=datetime.now()))

    @staticmethod
    def release_amount_in_session(s, entity_type: str, entity_id: int, amount: float) -> float:
        """Free `amount` of advance from a document, newest use first (splitting one if needed); returns what was freed.
        Used when a single 'paid from advance' payment row is voided."""
        remaining = round(float(amount or 0), 2)
        freed = 0.0
        rows = (s.query(AdvanceAllocation)
                .filter(AdvanceAllocation.entity_type == entity_type, AdvanceAllocation.entity_id == int(entity_id))
                .order_by(AdvanceAllocation.id.desc()).all())
        for a in rows:
            if remaining <= 0.004:
                break
            have = float(a.amount or 0)
            if have <= remaining + 0.004:
                s.delete(a)
                remaining -= have
                freed += have
            else:
                a.amount = round(have - remaining, 2)
                freed += remaining
                remaining = 0.0
        return round(freed, 2)

    @staticmethod
    def list_documents_with_advance(party_id: int, entity_type: str, limit: int = 300):
        """Purchases (entity_type="purchase") or sales ("sale") of this party that have advance money on them -
        what the Revert tab lists. Newest first; voided documents are left out."""
        from database.models.purchase import Purchase
        from database.models.sale import Sale
        from database.services.document_balance import document_balances

        entity_type = "purchase" if entity_type == "purchase" else "sale"
        model, col, prefix = ((Purchase, Purchase.supplier_party_id, "PUR") if entity_type == "purchase"
                              else (Sale, Sale.buyer_party_id, "SAL"))
        out = []
        with get_session() as s:
            ids = [int(i) for (i,) in
                   s.query(AdvanceAllocation.entity_id)
                   .filter(AdvanceAllocation.entity_type == entity_type).distinct().all()]
            if not ids:
                return out
            docs = (s.query(model).filter(col == int(party_id), model.id.in_(ids), model.status != "voided")
                    .order_by(model.id.desc()).limit(limit).all())
            bal = document_balances(s, entity_type, [int(d.id) for d in docs])
            for d in docs:
                uses = (s.query(AdvanceAllocation).filter(AdvanceAllocation.entity_type == entity_type,
                                                          AdvanceAllocation.entity_id == int(d.id))
                        .order_by(AdvanceAllocation.id).all())
                b = bal.get(int(d.id))
                out.append({
                    "id": int(d.id), "reference": f"{prefix}-{int(d.id)}", "created_at": d.created_at,
                    "total": float(b.net_total) if b else float(d.total_amount or 0),
                    "advance_used": round(sum(float(u.amount or 0) for u in uses), 2),
                    "uses": [{"allocation_id": int(u.id), "advance_id": int(u.advance_id),
                              "amount": float(u.amount or 0)} for u in uses],
                    "cash_paid": float(b.cash_paid) if b else 0.0,
                    "balance": float(b.balance) if b else 0.0,
                    "notes": d.notes or "",
                })
        return out

    @staticmethod
    @require_admin
    def revert_advance_use(entity_type: str, entity_id: int, user_id: int, reason: str = "") -> Dict:
        """
        Undo the advance used on one purchase / sale WITHOUT cancelling the document: the advance becomes unused
        again (available for another purchase / sale or to keep), and this document owes that amount again.
        Idempotent - a document with no advance on it just reports 0 released.
        """
        entity_type = "purchase" if entity_type == "purchase" else "sale"
        with get_session() as s:
            res = AdvanceService.release_in_session(s, entity_type, int(entity_id), int(user_id or 0),
                                                    reason or "Advance taken back")
            if res["released"] > 0.004:
                AuditService.record(
                    "advance.revert_use", entity_type, int(entity_id),
                    f"Took {res['released']:,.2f} of advance back off {entity_type} #{entity_id}",
                    {"reason": reason, "released": res["released"],
                     "advances": [{"advance_id": u["advance_id"], "amount": u["amount"]} for u in res["uses"]]},
                    session=s)
            s.commit()
        return {"released": res["released"], "advances": sorted({u["advance_id"] for u in res["uses"]})}
