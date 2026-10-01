# database/services/drawer/ledger.py
"""Persistent cross-cashier settlement ledger: sync from payments, settle one line / settle between two cashiers."""
from __future__ import annotations

from ._common import (
    Any,
    AuditService,
    CashierSettlement,
    Dict,
    IntegrityError,
    Optional,
    Payment,
    _f,
    date,
    datetime,
    func,
    get_session,
    select,
)


class LedgerMixin:

    # ------------------------------------------------------------------
    # Phase 2: persistent ledger sync (idempotent on source_payment_id)
    # ------------------------------------------------------------------
    @staticmethod
    def _sync_ledger(s, resolve, prefetch=None) -> None:
        """
        Mirror every cross-cashier obligation into cashier_settlements.

        INCREMENTAL, not a full re-scan: reads the highest source_payment_id
        already mirrored (one indexed MAX aggregate), then only examines
        Payment rows newer than that. The previous version re-scanned the
        ENTIRE payments table AND the entire cashier_settlements table on
        every single call - harmless at a few hundred rows, but compute()
        runs on every drawer load, every date-range change, and every
        language toggle, so at real volume (hundreds of thousands of rows,
        per the client's own stated growth) this was the single most
        expensive thing this screen did, and it would only get worse.

        Safe because payments are immutable once created in this system -
        a payment's creator/settler never change after the fact, so a
        payment already correctly excluded (same creator/settler, or no
        resolvable creator) stays correctly excluded forever; there is no
        need to keep re-examining it once a later payment has synced.

        Runs GLOBALLY - never date-filtered. The ledger is a permanent record,
        not a view; a payment must land here regardless of which range the
        screen happens to be showing. Idempotent on source_payment_id, so an
        already-settled row is NEVER reopened, rewritten or duplicated.
        """
        last_synced = s.execute(
            select(func.max(CashierSettlement.source_payment_id))
        ).scalar() or 0

        stmt = (
            select(Payment)
            .where(Payment.id > int(last_synced))
            .where(func.lower(func.coalesce(Payment.method, "")) != "advance")
            .where(Payment.status != "voided")
            .order_by(Payment.id)
        )

        new_rows = s.execute(stmt).scalars().all()
        if not new_rows:
            return
        if prefetch is not None:
            prefetch(new_rows)

        added = 0
        for p in new_rows:
            pid = int(p.id)
            settler = int(p.created_by_user_id or 0)
            et = str(p.entity_type or "")
            creator = resolve(et, p.entity_id)[0]
            if not creator or creator == settler:
                continue
            # the one law, identical to section C below
            if p.direction == "out":
                debtor, creditor = creator, settler
            else:
                debtor, creditor = settler, creator
            s.add(CashierSettlement(
                source_payment_id=pid,
                entity_type=et,
                entity_id=int(p.entity_id or 0),
                debtor_user_id=debtor,
                creditor_user_id=creditor,
                amount=_f(p.amount),
                status="open",
                note="",
                created_at=p.created_at or datetime.now(),
            ))
            added += 1

        if not added:
            return
        try:
            s.flush()
        except IntegrityError:
            # Another PC on the LAN inserted the same payment first. Its rows
            # are just as valid as ours, so drop ours and read theirs.
            s.rollback()

    # ------------------------------------------------------------------
    # Phase 2: settling
    # ------------------------------------------------------------------
    # NOTE ON THE MATH: settling does NOT change net_position, and that is
    # correct. Inter-cashier cash is drawer-to-drawer only - it writes no
    # Payment row - so cash_handled_net cannot move when a settlement is paid.
    # net_position is therefore the TERMINAL position ("what you are worth once
    # everything is squared"), which is exactly what the blessed test asserted
    # with "solunex NET = 0 - only moved others' money, owes nothing once
    # settled". Settling only clears the verdicts (the do-this-now list).
    #
    # Permission is deliberately NOT enforced here - settled_by_user_id is
    # whoever the UI says it is. Gate the button, not the service, until the
    # admin-only decision is made.
    # ------------------------------------------------------------------
    @staticmethod
    def settle(settlement_id: int,
               settled_by_user_id: int,
               note: Optional[str] = None) -> Dict[str, Any]:
        """Settle ONE ledger row (the detail table's per-line action)."""
        with get_session() as s:
            row = s.get(CashierSettlement, int(settlement_id))
            if row is None:
                return {"ok": False, "error": "not_found", "settled": 0}
            if str(row.status) == "settled":
                return {"ok": True, "already": True, "settled": 0}
            row.status = "settled"
            row.settled_by_user_id = int(settled_by_user_id)
            row.settled_at = datetime.now()
            if note:
                row.note = str(note)[:255]
            AuditService.record(
                "drawer.settle", "cashier_settlement", int(settlement_id),
                f"Settled {float(row.amount or 0):,.2f} (user {row.debtor_user_id} -> {row.creditor_user_id})",
                {"debtor": row.debtor_user_id, "creditor": row.creditor_user_id,
                 "amount": float(row.amount or 0), "note": note}, session=s)
            return {"ok": True, "already": False, "settled": 1}

    @staticmethod
    def settle_with(user_id: int,
                    other_user_id: int,
                    settled_by_user_id: int,
                    note: Optional[str] = None) -> Dict[str, Any]:
        """
        Settle the NET between two cashiers: mark every open row between them
        settled, in BOTH directions.

        This is what the verdict's Settle button calls. A verdict is a net
        ("Give Abdoull N100,000") that nets rows running both ways, so paying
        it discharges all of them - settling only one direction would leave a
        phantom obligation behind.
        """
        a, b = int(user_id), int(other_user_id)
        if a == b:
            return {"ok": False, "error": "same_user", "settled": 0}
        with get_session() as s:
            rows = s.execute(select(CashierSettlement).where(
                CashierSettlement.status == "open",
                CashierSettlement.debtor_user_id.in_([a, b]),
                CashierSettlement.creditor_user_id.in_([a, b]),
            )).scalars().all()
            now = datetime.now()
            n = 0
            for r in rows:
                if {int(r.debtor_user_id), int(r.creditor_user_id)} != {a, b}:
                    continue
                r.status = "settled"
                r.settled_by_user_id = int(settled_by_user_id)
                r.settled_at = now
                if note:
                    r.note = str(note)[:255]
                n += 1
            if n:
                AuditService.record(
                    "drawer.settle_with", "user", b,
                    f"Settled {n} line(s) between users {a} and {b}",
                    {"user_a": a, "user_b": b, "lines": n, "note": note}, session=s)
            return {"ok": True, "settled": n}
