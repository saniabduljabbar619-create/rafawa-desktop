# database/services/party_ledger_service.py

from __future__ import annotations

from typing import List, Dict, Optional
from datetime import date, datetime as _dt

from sqlalchemy import and_, false as sa_false, func, or_, true as sa_true

from database.models.party import Party
from database.session import get_session
from database.services.document_balance import document_balances, offset_totals
from shared.config.constants import WALKIN_PARTY_ID

from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.product import Product

from database.models.sale import Sale
from database.models.sale_item import SaleItem

from database.models.purchase_return import PurchaseReturn
from database.models.purchase_price_adjustment import PurchasePriceAdjustment
from database.models.sale_return import SaleReturn

from database.models.payment import Payment
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.sale_discount import SaleDiscount


def _m(v) -> str:
    return f"\u20a6{float(v or 0):,.2f}"


def _advance_detail(a, amount: float, given: bool, uses, cash_settled: float, used: float, voided: bool,
                    user: str) -> Dict[str, str]:
    """Every fact about one advance, in words: which way, how much, what it was used on, what is left."""
    way = ("Advance OUT - money we gave to the party (to be set against what we owe them)" if given
           else "Advance IN - money the party gave us (to be set against what they owe us)")
    parts = [f"{way}: {_m(amount)}"]
    if a.ledger:
        parts.append(f"Ledger: {a.ledger}")
    if voided:
        parts.append("VOIDED - no longer counts in any balance")
    else:
        if uses or cash_settled:
            bits = [f"{'PUR' if u.entity_type == 'purchase' else 'SAL'}-{int(u.entity_id)} {_m(u.amount)}" for u in uses]
            if cash_settled:
                bits.append(f"settled in cash/transfer {_m(cash_settled)}")
            parts.append(f"Used {_m(used)}: " + ", ".join(bits))
        else:
            parts.append("Not used yet")
        parts.append(f"Still unused: {_m(max(amount - used, 0.0))}")
    parts.append("Recorded " + (f"by {user}" if user else "by the system"))
    if a.notes:
        parts.append(f"Note: {a.notes}")
    summary = f"{'Advance OUT' if given else 'Advance IN'} {_m(amount)}"
    if voided:
        summary += " - VOIDED"
    elif used and used >= amount - 0.005:
        summary += " - fully used"
    elif used:
        summary += f" - {_m(used)} used, {_m(amount - used)} unused"
    else:
        summary += " - unused"
    if a.notes:
        summary += f"  |  {a.notes}"
    return {"summary": summary, "text": "\n".join(parts)}


def _payment_detail(pay, ref: str, amount: float, is_sale: bool, what: str, bal, net_total, adv_sources,
                    user: str) -> Dict[str, str]:
    """Every fact about one payment, in words: who paid whom, how, for what, and what it left owing."""
    method = (pay.method or "").lower()
    word = {"cash": "Cash", "transfer": "Bank transfer", "bank": "Bank", "pos": "POS", "mobile": "Mobile money",
            "cheque": "Cheque", "advance": "From advance"}.get(method, method.capitalize() or "Payment")
    if is_sale:
        head = f"{word}: customer paid us {_m(amount)} for {ref}"
    else:
        head = f"{word}: we paid the supplier {_m(amount)} for {ref}"
    if method == "advance" and adv_sources:
        head = f"From advance: {_m(amount)} of {', '.join(adv_sources)} set against {ref}"
    parts = [head]
    if what:
        parts.append(f"For: {what}")
    if net_total is not None:
        parts.append(f"{ref} total: {_m(net_total)}")
    if bal:
        parts.append(f"Owed before this payment: {_m(bal['before'])}  |  after: {_m(bal['after'])}")
    parts.append("Recorded " + (f"by {user}" if user else "by the system"))
    if pay.notes:
        parts.append(f"Note: {pay.notes}")
    summary = head + (f"  |  {what}" if what else "")
    if bal:
        summary += f"  |  owed {_m(bal['before'])} -> {_m(bal['after'])}"
    if pay.notes and method != "advance":
        summary += f"  |  {pay.notes}"
    return {"summary": summary, "text": "\n".join(parts)}


def _doc_payment_status(net_total: float, settled: float, balance: float) -> str:
    if net_total <= 0:
        return "paid"
    if settled <= 0:
        return "unpaid"
    if balance > 0:
        return "partial"
    return "paid"


def _group_activity(rows: List[Dict]) -> List[Dict]:
    """
    Newest first, but a sale/purchase is followed directly by its own payments (oldest first), so each document
    "carries its tail". A group is dated by its latest event. A settlement marker sorts ABOVE the row that
    settled the balance (same instant), so nothing sits after a marker unless it is genuinely newer activity.
    """
    docs = {r["reference"]: r for r in rows if r.get("type") in ("sale", "purchase")}
    tails: Dict[str, List[Dict]] = {}
    singles: List[Dict] = []
    for r in rows:
        if r.get("type") == "payment" and r.get("reference") in docs:
            tails.setdefault(r["reference"], []).append(r)
        elif r.get("type") not in ("sale", "purchase"):
            singles.append(r)

    def _t(r):
        return r.get("dt") or _dt.min

    groups = []                                  # (anchor, priority, [rows])
    for ref, doc in docs.items():
        pays = sorted(tails.get(ref, []), key=_t)
        anchor = max([_t(doc)] + [_t(p) for p in pays])
        groups.append((anchor, 1, [doc] + pays))
    for r in singles:
        groups.append((_t(r), 2 if r.get("type") == "settlement_marker" else 1, [r]))
    groups.sort(key=lambda g: (g[0], g[1]), reverse=True)
    return [r for _a, _p, rs in groups for r in rs]


def _party_wide_running_balances(
    s,
    entity_type: str,
    entity_ids: List[int],
    net_totals: Dict[int, float],
    creation_dts: Dict[int, object],
):
    """
    A payment made against a party isn't necessarily tied to one specific
    sale/purchase row - the Payment Center pays down whatever's
    outstanding across ALL of that party's sales (or all of their
    purchases) at once, same as a real bank statement tracks one running
    balance, not a separate one per transaction. So this walks a single
    combined timeline for the whole entity_type (every document's own
    total joining the pool the moment it's created, every cash payment
    and every advance allocation draining it) and returns:

      - payment_balances: {payment_id: {"before": x, "after": y}} - the
        COMBINED outstanding balance across every sale/purchase for this
        party, immediately before and after that one specific payment.
      - settlement_dts: every timestamp at which that combined balance
        dropped to exactly 0.0 (crossed down from something owed to
        nothing owed) - used to place a single "Settled" marker there.
        Since new sales/purchases can reopen the balance later, this can
        fire more than once over a party's history.
    """
    payment_balances: Dict[int, Dict[str, float]] = {}
    settlement_dts: List[object] = []
    if not entity_ids:
        return payment_balances, settlement_dts

    direction = "in" if entity_type == "sale" else "out"

    cash_payments = (
        s.query(Payment)
        .filter(Payment.entity_type == entity_type, Payment.entity_id.in_(entity_ids))
        .filter(Payment.direction == direction, Payment.method != "advance")
        .filter(~Payment.notes.like("%Advance settlement%"))
        .filter(Payment.status != "voided")
        .all()
    )
    allocations = (
        s.query(AdvanceAllocation)
        .filter(AdvanceAllocation.entity_type == entity_type, AdvanceAllocation.entity_id.in_(entity_ids))
        .all()
    )

    # events: (dt, priority, delta, payment_id) - "add" events (a document
    # joining the pool) sort before "settle" events (something draining
    # it) on a tie, so a same-instant sale + payment reads naturally.
    events: List[tuple] = []
    for eid in entity_ids:
        amt = float(net_totals.get(eid, 0.0))
        if amt > 0:
            events.append((creation_dts.get(eid) or _dt.min, 0, amt, None))
    for p in cash_payments:
        events.append((p.created_at or _dt.min, 1, -float(p.amount or 0), int(p.id)))
    for a in allocations:
        events.append((getattr(a, "created_at", None) or _dt.min, 1, -float(a.amount or 0), None))

    events.sort(key=lambda e: (e[0], e[1]))

    running = 0.0
    for dt, priority, delta, pay_id in events:
        before = running
        running = running + delta
        if priority == 1:
            running = max(running, 0.0)
        after = running

        if priority == 1 and pay_id is not None:
            payment_balances[pay_id] = {"before": before, "after": after}
        if priority == 1 and before > 0.0 and after <= 0.0:
            settlement_dts.append(dt)

    return payment_balances, settlement_dts


class PartyLedgerService:

    @staticmethod
    def _net_totals_for_sales(s, sale_ids: List[int]) -> Dict[int, float]:
        # A voided sale owes nothing; discounts/returns netted - see document_balance.
        bals = document_balances(s, "sale", sale_ids)
        return {int(sid): (bals[int(sid)].net_total if int(sid) in bals else 0.0) for sid in sale_ids}

    @staticmethod
    def _net_totals_for_purchases(s, purchase_ids: List[int]) -> Dict[int, float]:
        bals = document_balances(s, "purchase", purchase_ids)
        return {int(pid): (bals[int(pid)].net_total if int(pid) in bals else 0.0) for pid in purchase_ids}

    @staticmethod
    def payment_document_balances(party_id: int) -> Dict[int, Dict[str, float]]:
        """
        Public entry point: for every real cash Payment against a sale or
        purchase belonging to this party, returns the COMBINED balance
        across every one of that party's sales (or every one of their
        purchases) immediately before / after that specific payment -
        matching the Payment Center, where a payment isn't tied to one
        specific row. The Payments tab uses this so it shows exactly the
        same numbers as the Activity tab, both reading from this single
        place.
        """
        with get_session() as s:
            sale_rows = s.query(Sale.id, Sale.completed_at).filter(Sale.buyer_party_id == party_id).all()
            purchase_rows = s.query(Purchase.id, Purchase.completed_at).filter(Purchase.supplier_party_id == party_id).all()
            sale_ids = [int(sid) for sid, _ in sale_rows]
            purchase_ids = [int(pid) for pid, _ in purchase_rows]
            sale_creation_dt = {int(sid): dt for sid, dt in sale_rows}
            pur_creation_dt = {int(pid): dt for pid, dt in purchase_rows}

            sale_net_totals = PartyLedgerService._net_totals_for_sales(s, sale_ids)
            pur_net_totals = PartyLedgerService._net_totals_for_purchases(s, purchase_ids)

            sale_balances, _ = _party_wide_running_balances(s, "sale", sale_ids, sale_net_totals, sale_creation_dt)
            pur_balances, _ = _party_wide_running_balances(s, "purchase", purchase_ids, pur_net_totals, pur_creation_dt)

            result: Dict[int, Dict[str, float]] = {}
            result.update(sale_balances)
            result.update(pur_balances)
            return result

    @staticmethod
    def list_activity_details(
        party_id: int,
        limit: int = 500,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        walkin_name: Optional[str] = None,
    ) -> List[Dict]:
        """
        walkin_name: for the shared walk-in account only - restrict everything to that one walk-in's documents.

        One row per REAL business event. A sale/purchase's payments are no
        longer split out as their own separate rows here - each sale and
        purchase carries its own running Paid/Balance/Status instead (the
        same trusted, batched formula used everywhere else in this app:
        net_total = raw_total - returns[- discount], settled = cash paid +
        advance-allocated, balance = max(net_total - settled, 0)), so one
        row tracks that document end-to-end instead of scattering its
        payments across the ledger as unrelated-looking entries.
        Returns and advances stay as their own individual postings.
        """

        def _in_range(dt) -> bool:
            if not start_date and not end_date:
                return True
            if dt is None:
                return False
            d = dt.date() if hasattr(dt, "date") else dt
            if start_date and d < start_date:
                return False
            if end_date and d > end_date:
                return False
            return True

        rows: List[Dict] = []

        with get_session() as s:

            # --------------------------------------------------
            # PURCHASES (payment info folded in - see _doc_payment_status)
            # --------------------------------------------------

            purchases = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(Purchase.walkin_name == walkin_name if walkin_name else sa_true())
                .all()
            )
            purchase_ids = [int(p.id) for p in purchases]

            pur_ledger: Dict[int, str] = {}
            if purchase_ids:
                for pid, ptype in (
                    s.query(PurchaseItem.purchase_id, PurchaseItem.product_type_snapshot)
                    .filter(PurchaseItem.purchase_id.in_(purchase_ids))
                    .all()
                ):
                    pur_ledger.setdefault(int(pid), ptype or "crops")

            # Full, untruncated item breakdown per purchase - for the
            # Excel export's "Items" column. Never shown in the on-screen
            # table (which stays exactly as it was), only carried in the
            # data for whoever exports it.
            pur_items_detail: Dict[int, str] = {}
            if purchase_ids:
                product_names = {p.id: p.name for p in s.query(Product).all()}
                pur_item_rows: Dict[int, List[str]] = {}
                for it in (
                    s.query(PurchaseItem)
                    .filter(PurchaseItem.purchase_id.in_(purchase_ids))
                    .all()
                ):
                    pid = int(it.purchase_id)
                    name = product_names.get(it.product_id, f"Product#{it.product_id}")
                    qty = f"{float(it.total_weight_kg):,.0f}kg" if float(it.total_weight_kg or 0) > 0 else f"{int(it.qty_bags)}bg"
                    pur_item_rows.setdefault(pid, []).append(f"{name} ({qty})")
                pur_items_detail = {pid: "; ".join(lines) for pid, lines in pur_item_rows.items()}

            pur_returns: Dict[int, float] = {}
            pur_adjustments: Dict[int, float] = {}
            pur_paid: Dict[int, float] = {}
            pur_alloc: Dict[int, float] = {}
            if purchase_ids:
                for pid, amt in (
                    s.query(PurchaseReturn.purchase_id, func.coalesce(func.sum(PurchaseReturn.amount), 0))
                    .filter(PurchaseReturn.purchase_id.in_(purchase_ids))
                    .group_by(PurchaseReturn.purchase_id).all()
                ):
                    pur_returns[int(pid)] = abs(float(amt or 0))

                # Same reasoning as returns above - a price adjustment is
                # a real, additional amount now genuinely owed (e.g. a
                # supplier renegotiating after delivery), so the Activity
                # tab's own row needs to know about it directly, not
                # just the summary cards or the payment dialog elsewhere
                # in the app.
                for pid, amt in (
                    s.query(PurchasePriceAdjustment.purchase_id, func.coalesce(func.sum(PurchasePriceAdjustment.amount), 0))
                    .filter(PurchasePriceAdjustment.purchase_id.in_(purchase_ids))
                    .group_by(PurchasePriceAdjustment.purchase_id).all()
                ):
                    pur_adjustments[int(pid)] = float(amt or 0)

                for pid, amt in (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "purchase", Payment.entity_id.in_(purchase_ids))
                    .filter(Payment.direction == "out", Payment.method != "advance")
                    .filter(~Payment.notes.like("%Advance settlement%"))
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                ):
                    pur_paid[int(pid)] = float(amt or 0)

                for pid, amt in (
                    s.query(AdvanceAllocation.entity_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                    .filter(AdvanceAllocation.entity_type == "purchase", AdvanceAllocation.entity_id.in_(purchase_ids))
                    .group_by(AdvanceAllocation.entity_id).all()
                ):
                    pur_alloc[int(pid)] = float(amt or 0)
                for pid, amt in offset_totals(s, "purchase", purchase_ids).items():
                    pur_alloc[pid] = pur_alloc.get(pid, 0.0) + amt          # offsets settle like advances

            pur_net_totals: Dict[int, float] = {}
            pur_creation_dt: Dict[int, object] = {}
            for p in purchases:
                pid = int(p.id)
                is_void = (p.status == "voided")
                raw_total = 0.0 if is_void else float(p.total_amount or 0)
                returned = pur_returns.get(pid, 0.0)
                adjustment_total = pur_adjustments.get(pid, 0.0)
                net_total = max(raw_total - returned + adjustment_total, 0.0)
                pur_net_totals[pid] = net_total
                pur_creation_dt[pid] = p.completed_at

                if not _in_range(p.completed_at):
                    continue

                settled = pur_paid.get(pid, 0.0) + pur_alloc.get(pid, 0.0)
                balance = max(net_total - settled, 0.0)

                rows.append({
                    "dt": p.completed_at,
                    "ledger": pur_ledger.get(pid, "crops"),
                    "type": "purchase",
                    "reference": f"PUR-{p.id}",
                    "summary": "Purchase",
                    "total": net_total,
                    "paid": min(settled, net_total),
                    "balance": balance,
                    "status": "voided" if is_void else _doc_payment_status(net_total, settled, balance),
                    "items": pur_items_detail.get(pid, ""),
                })

            # --------------------------------------------------
            # SALES (same treatment)
            # --------------------------------------------------

            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == party_id)
                .filter(Sale.walkin_name == walkin_name if walkin_name else sa_true())
                .all()
            )
            sale_ids = [int(s1.id) for s1 in sales]

            sale_ledger: Dict[int, str] = {}
            if sale_ids:
                for sid, stype in (
                    s.query(SaleItem.sale_id, SaleItem.product_type_snapshot)
                    .filter(SaleItem.sale_id.in_(sale_ids))
                    .all()
                ):
                    sale_ledger.setdefault(int(sid), stype or "crops")

            # Full, untruncated item breakdown per sale - mirrors
            # pur_items_detail above, same reasoning, sale side.
            sale_items_detail: Dict[int, str] = {}
            if sale_ids:
                product_names = {p.id: p.name for p in s.query(Product).all()}
                sale_item_rows: Dict[int, List[str]] = {}
                for it in (
                    s.query(SaleItem)
                    .filter(SaleItem.sale_id.in_(sale_ids))
                    .all()
                ):
                    sid = int(it.sale_id)
                    name = product_names.get(it.product_id, f"Product#{it.product_id}")
                    qty = f"{float(it.total_weight_kg):,.0f}kg" if float(it.total_weight_kg or 0) > 0 else f"{int(it.qty_bags)}bg"
                    sale_item_rows.setdefault(sid, []).append(f"{name} ({qty})")
                sale_items_detail = {sid: "; ".join(lines) for sid, lines in sale_item_rows.items()}

            sale_returns: Dict[int, float] = {}
            sale_paid: Dict[int, float] = {}
            sale_alloc: Dict[int, float] = {}
            sale_discounts: Dict[int, float] = {}
            if sale_ids:
                for sid, amt in (
                    s.query(SaleReturn.sale_id, func.coalesce(func.sum(SaleReturn.amount), 0))
                    .filter(SaleReturn.sale_id.in_(sale_ids))
                    .group_by(SaleReturn.sale_id).all()
                ):
                    sale_returns[int(sid)] = abs(float(amt or 0))

                for sid, amt in (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "sale", Payment.entity_id.in_(sale_ids))
                    .filter(Payment.direction == "in", Payment.method != "advance")
                    .filter(~Payment.notes.like("%Advance settlement%"))
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                ):
                    sale_paid[int(sid)] = float(amt or 0)

                for sid, amt in (
                    s.query(AdvanceAllocation.entity_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                    .filter(AdvanceAllocation.entity_type == "sale", AdvanceAllocation.entity_id.in_(sale_ids))
                    .group_by(AdvanceAllocation.entity_id).all()
                ):
                    sale_alloc[int(sid)] = float(amt or 0)
                for sid, amt in offset_totals(s, "sale", sale_ids).items():
                    sale_alloc[sid] = sale_alloc.get(sid, 0.0) + amt

                for sid, amt in (
                    s.query(SaleDiscount.sale_id, func.coalesce(func.sum(SaleDiscount.amount), 0))
                    .filter(SaleDiscount.sale_id.in_(sale_ids))
                    .group_by(SaleDiscount.sale_id).all()
                ):
                    sale_discounts[int(sid)] = float(amt or 0)

            sale_net_totals: Dict[int, float] = {}
            sale_creation_dt: Dict[int, object] = {}
            for s1 in sales:
                sid = int(s1.id)
                is_void = (s1.status == "voided")
                raw_total = 0.0 if is_void else float(s1.total_amount or 0)
                returned = sale_returns.get(sid, 0.0)
                discount = sale_discounts.get(sid, 0.0)
                net_total = max(raw_total - returned - discount, 0.0)
                sale_net_totals[sid] = net_total
                sale_creation_dt[sid] = s1.completed_at

                if not _in_range(s1.completed_at):
                    continue

                settled = sale_paid.get(sid, 0.0) + sale_alloc.get(sid, 0.0)
                balance = max(net_total - settled, 0.0)

                rows.append({
                    "dt": s1.completed_at,
                    "ledger": sale_ledger.get(sid, "crops"),
                    "type": "sale",
                    "reference": f"SAL-{s1.id}",
                    "summary": "Sale",
                    "total": net_total,
                    "paid": min(settled, net_total),
                    "balance": balance,
                    "status": "voided" if is_void else _doc_payment_status(net_total, settled, balance),
                    "items": sale_items_detail.get(sid, ""),
                })

            # --------------------------------------------------
            # PURCHASE RETURNS - stay as their own individual postings
            # --------------------------------------------------

            pr = (
                s.query(PurchaseReturn)
                .filter(PurchaseReturn.party_id == party_id)
                .filter(PurchaseReturn.purchase_id.in_(purchase_ids or [0]) if walkin_name else sa_true())
                .all()
            )

            for r in pr:
                if not _in_range(r.created_at):
                    continue
                amt = abs(float(r.amount or 0))
                rows.append({
                    "dt": r.created_at,
                    "ledger": r.product_type_snapshot,
                    "type": "purchase_return",
                    "reference": f"PRET-{r.id}",
                    "summary": "Purchase Return",
                    "total": amt,
                    "paid": None,
                    "balance": None,
                    "status": "posted",
                })

            # --------------------------------------------------
            # SALE RETURNS - stay as their own individual postings
            # --------------------------------------------------

            sr = (
                s.query(SaleReturn)
                .filter(SaleReturn.party_id == party_id)
                .filter(SaleReturn.sale_id.in_(sale_ids or [0]) if walkin_name else sa_true())
                .all()
            )

            for r in sr:
                if not _in_range(r.created_at):
                    continue
                amt = abs(float(r.amount or 0))
                rows.append({
                    "dt": r.created_at,
                    "ledger": r.product_type_snapshot,
                    "type": "sale_return",
                    "reference": f"SRET-{r.id}",
                    "summary": "Sale Return",
                    "total": amt,
                    "paid": None,
                    "balance": None,
                    "status": "posted",
                })

            # --------------------------------------------------
            # ADVANCES - stay as their own individual postings
            # --------------------------------------------------

            adv = (
                s.query(Advance)
                .filter(Advance.party_id == party_id)
                .filter(sa_false() if walkin_name else sa_true())          # a walk-in has no advance account
                .all()
            )

            from database.models.user import User
            adv_ids = [int(a.id) for a in adv]
            adv_uses: Dict[int, List] = {}
            for al in (s.query(AdvanceAllocation).filter(AdvanceAllocation.advance_id.in_(adv_ids or [0]))
                       .order_by(AdvanceAllocation.id).all()):
                adv_uses.setdefault(int(al.advance_id), []).append(al)
            adv_cash: Dict[int, float] = {}
            for eid, amt in (s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                             .filter(Payment.entity_type == "advance", Payment.entity_id.in_(adv_ids or [0]))
                             .filter(Payment.status != "voided").group_by(Payment.entity_id).all()):
                adv_cash[int(eid)] = float(amt or 0)
            adv_users = {int(u.id): u.username for u in s.query(User).filter(
                User.id.in_({int(a.created_by_user_id) for a in adv if a.created_by_user_id} or {0})).all()}

            for a in adv:
                if not _in_range(a.created_at):
                    continue
                amt = float(a.amount or 0)
                given = (a.direction or "").lower() == "given"
                uses = adv_uses.get(int(a.id), [])
                used = sum(float(u.amount or 0) for u in uses) + adv_cash.get(int(a.id), 0.0)
                voided = (a.status or "") == "voided"
                detail = _advance_detail(a, amt, given, uses, adv_cash.get(int(a.id), 0.0), used, voided,
                                         adv_users.get(int(a.created_by_user_id or 0), ""))

                # normalize direction
                if given:
                    amt = -amt

                rows.append({
                    "dt": a.created_at,
                    "ledger": "money",
                    "type": "advance",
                    "reference": f"ADV-{a.id}",
                    "summary": detail["summary"],
                    "detail": detail["text"],
                    "total": amt,
                    "paid": None if voided else used,                  # how much of it has been used
                    "balance": None if voided else max(abs(amt) - used, 0.0),   # how much is still unused
                    "status": a.status,
                    "operator": adv_users.get(int(a.created_by_user_id or 0), ""),
                })

        # --------------------------------------------------
        # INDIVIDUAL PAYMENT ROWS - one per real Payment record, each
        # clearly labeled back to its parent document (e.g. "Payment -
        # SAL-1"). Added ALONGSIDE the combined summary rows above (which
        # still carry the document's own running Paid/Balance/Status) -
        # this doesn't replace that, it makes each individual payment
        # event visible and traceable to its parent, no matter how much
        # time passed between the sale/purchase and its payment. Sorted
        # the same newest-first way as everything else, so a payment
        # naturally lands right next to its own document in the common
        # case where nothing else happened for this party in between.
        with get_session() as s:
            from database.models.user import User

            sale_no_by_id = {int(sid): f"SAL-{sid}" for sid in sale_ids} if sale_ids else {}
            pur_no_by_id = {int(p.id): f"PUR-{p.id}" for p in purchases}

            pay_rows = (
                s.query(Payment)
                .filter(Payment.party_id == party_id)
                .filter(Payment.entity_type.in_(("sale", "purchase")))
                .filter(Payment.status != "voided")
                .filter(or_(and_(Payment.entity_type == "sale", Payment.entity_id.in_(sale_ids or [0])),
                            and_(Payment.entity_type == "purchase", Payment.entity_id.in_(purchase_ids or [0])))
                        if walkin_name else sa_true())
                .all()
            )
            pay_adv_sources: Dict[tuple, List[str]] = {}
            if any((p.method or "") == "advance" for p in pay_rows):
                for al in (s.query(AdvanceAllocation).filter(AdvanceAllocation.entity_type.in_(("sale", "purchase")))
                           .filter(or_(and_(AdvanceAllocation.entity_type == "sale",
                                            AdvanceAllocation.entity_id.in_(sale_ids or [0])),
                                       and_(AdvanceAllocation.entity_type == "purchase",
                                            AdvanceAllocation.entity_id.in_(purchase_ids or [0]))))
                           .order_by(AdvanceAllocation.id).all()):
                    pay_adv_sources.setdefault((al.entity_type, int(al.entity_id)), []).append(
                        f"ADV-{int(al.advance_id)} (\u20a6{float(al.amount or 0):,.2f})")
            payer_ids = {int(p.created_by_user_id) for p in pay_rows if p.created_by_user_id}
            payer_names = {
                int(u.id): u.username for u in s.query(User).filter(User.id.in_(payer_ids)).all()
            } if payer_ids else {}

            # Combined party-wide running balance across every sale (and,
            # separately, every purchase) for this party - a payment
            # isn't tied to one specific row, it pays down whatever's
            # outstanding overall. Also returns the exact timestamps
            # where that combined balance hit 0.0, for the settlement
            # markers below. See _party_wide_running_balances.
            sale_pay_balances, sale_settle_dts = _party_wide_running_balances(
                s, "sale", sale_ids, sale_net_totals, sale_creation_dt
            )
            pur_pay_balances, pur_settle_dts = _party_wide_running_balances(
                s, "purchase", purchase_ids, pur_net_totals, pur_creation_dt
            )

            for pay in pay_rows:
                if not _in_range(pay.created_at):
                    continue
                eid = int(pay.entity_id or 0)
                if pay.entity_type == "sale":
                    parent_ref = sale_no_by_id.get(eid, f"SAL-{eid}")
                    ledger = sale_ledger.get(eid, "crops")
                    bal = sale_pay_balances.get(int(pay.id))
                else:
                    parent_ref = pur_no_by_id.get(eid, f"PUR-{eid}")
                    ledger = pur_ledger.get(eid, "crops")
                    bal = pur_pay_balances.get(int(pay.id))

                amount_val = float(pay.amount or 0)
                is_sale = pay.entity_type == "sale"
                what = (sale_items_detail if is_sale else pur_items_detail).get(eid, "")
                detail = _payment_detail(
                    pay, parent_ref, amount_val, is_sale, what, bal,
                    (sale_net_totals if is_sale else pur_net_totals).get(eid),
                    pay_adv_sources.get((pay.entity_type, eid), []),
                    payer_names.get(int(pay.created_by_user_id or 0), ""))

                rows.append({
                    "dt": pay.created_at,
                    "ledger": ledger,
                    "type": "payment",
                    "reference": parent_ref,
                    "summary": detail["summary"],
                    "detail": detail["text"],
                    # "Total" here is the combined outstanding sum across
                    # every sale/purchase for the party right before this
                    # payment landed (same value as Balance Before) -
                    # "Paid" is this one transaction's own amount. None
                    # for advance-settlement-tagged payments, since those
                    # are counted through the allocation instead of as
                    # their own independent cash event (see helper).
                    "total": bal["before"] if bal else amount_val,
                    "paid": amount_val,
                    "balance": bal["after"] if bal else None,
                    "balance_before": bal["before"] if bal else None,
                    "balance_after": bal["after"] if bal else None,
                    "status": "",
                    "operator": payer_names.get(int(pay.created_by_user_id or 0), ""),
                })

        # --------------------------------------------------
        # SETTLEMENT MARKERS - fire once the COMBINED balance across the
        # party's sales (Receivables) or purchases (Payables) reaches
        # exactly 0.0 - not per individual document. Dated to the moment
        # that combined balance actually crossed to zero, so the marker
        # takes its natural place in the newest-first list: everything
        # that contributed sits below it, and only genuinely new activity
        # after that point ever appears above it.
        # --------------------------------------------------
        for dt in sale_settle_dts:
            if _in_range(dt):
                rows.append({
                    "dt": dt, "ledger": "", "type": "settlement_marker", "reference": "",
                    "summary": "Receivables Settled", "total": None, "paid": None, "balance": None,
                    "balance_before": None, "balance_after": None, "status": "receivables",
                })
        for dt in pur_settle_dts:
            if _in_range(dt):
                rows.append({
                    "dt": dt, "ledger": "", "type": "settlement_marker", "reference": "",
                    "summary": "Payables Settled", "total": None, "paid": None, "balance": None,
                    "balance_before": None, "balance_after": None, "status": "payables",
                })

        # weighings: which one a purchase came from, and (for a walk-in) the ones still waiting for the cashier
        from database.models.weigh_ticket import WeighTicket
        with get_session() as s:
            pur_refs = [int(r["reference"].split("-")[-1]) for r in rows
                        if r.get("type") == "purchase" and str(r.get("reference", "")).startswith("PUR-")]
            tix = {int(pid): (tno or f"WT-{int(tid):05d}") for pid, tid, tno in
                   s.query(WeighTicket.purchase_id, WeighTicket.id, WeighTicket.ticket_no)
                   .filter(WeighTicket.purchase_id.in_(pur_refs or [0])).all()}
            for r in rows:
                if r.get("type") == "purchase" and r.get("summary") == "Purchase":
                    tag = tix.get(int(str(r["reference"]).split("-")[-1]))
                    if tag:
                        r["summary"] = f"Purchase ({tag})"
            is_walkin = (s.query(Party.party_type).filter(Party.id == party_id).scalar() == "walkin")
            if is_walkin and not walkin_name:
                for tk in (s.query(WeighTicket).filter(WeighTicket.party_id == party_id)
                           .filter(WeighTicket.status.in_(("open", "submitted"))).all()):
                    if _in_range(tk.created_at):
                        rows.append({"dt": tk.created_at, "ledger": "crops", "type": "weighing",
                                     "reference": tk.ticket_no or f"WT-{int(tk.id):05d}",
                                     "summary": "Weighed - waiting for the cashier", "total": 0, "paid": None,
                                     "balance": None, "status": "pending"})
        return _group_activity(rows)[:limit]
    

    # --------------------------------------------------
    # CROP ACTIVITY LEDGER
    # --------------------------------------------------

    @staticmethod
    def list_crop_activity(party_id: int, limit: int = 500):

        from database.models.purchase import Purchase
        from database.models.purchase_item import PurchaseItem
        from database.models.sale import Sale
        from database.models.sale_item import SaleItem
        from database.models.purchase_return import PurchaseReturn
        from database.models.sale_return import SaleReturn
        from database.models.product import Product

        rows = []

        with get_session() as s:

            # --------------------------------------------------
            # PURCHASE ITEMS (CROPS ONLY)
            # --------------------------------------------------

            q = (
                s.query(
                    Purchase.completed_at,
                    Product.name,
                    PurchaseItem.qty_bags,
                    PurchaseItem.total_weight_kg,
                    PurchaseItem.unit_cost_rate,
                    PurchaseItem.line_total,
                    Purchase.id,
                    Purchase.status
                )
                .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
                .join(Product, Product.id == PurchaseItem.product_id)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(PurchaseItem.product_type_snapshot == "crops")
                .all()
            )

            for r in q:

                rows.append({
                    "dt": r[0],
                    "product": r[1],
                    "type": "purchase",
                    "reference": f"PUR-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": r[7]
                })

            # --------------------------------------------------
            # SALE ITEMS (CROPS ONLY)
            # --------------------------------------------------

            q = (
                s.query(
                    Sale.completed_at,
                    Product.name,
                    SaleItem.qty_bags,
                    SaleItem.total_weight_kg,
                    SaleItem.unit_sale_rate,
                    SaleItem.line_total,
                    Sale.id,
                    Sale.status
                )
                .join(Sale, Sale.id == SaleItem.sale_id)
                .join(Product, Product.id == SaleItem.product_id)
                .filter(Sale.buyer_party_id == party_id)
                .filter(SaleItem.product_type_snapshot == "crops")
                .all()
            )

            for r in q:

                rows.append({
                    "dt": r[0],
                    "product": r[1],
                    "type": "sale",
                    "reference": f"SAL-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": r[7]
                })

            # --------------------------------------------------
            # PURCHASE RETURNS (CROPS ONLY)
            # --------------------------------------------------

            q = (
                s.query(
                    PurchaseReturn.created_at,
                    Product.name,
                    PurchaseReturn.qty_bags,
                    PurchaseReturn.qty_kg,
                    PurchaseReturn.unit_cost_snapshot,
                    PurchaseReturn.amount,
                    PurchaseReturn.id
                )
                .join(Product, Product.id == PurchaseReturn.product_id)
                .filter(PurchaseReturn.party_id == party_id)
                .filter(PurchaseReturn.product_type_snapshot == "crops")
                .all()
            )

            for r in q:

                rows.append({
                    "dt": r[0],
                    "product": r[1],
                    "type": "purchase_return",
                    "reference": f"PRET-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": "posted"
                })

            # --------------------------------------------------
            # SALE RETURNS (CROPS ONLY)
            # --------------------------------------------------

            q = (
                s.query(
                    SaleReturn.created_at,
                    Product.name,
                    SaleReturn.qty_bags,
                    SaleReturn.qty_kg,
                    SaleReturn.unit_price_snapshot,
                    SaleReturn.amount,
                    SaleReturn.id
                )
                .join(Product, Product.id == SaleReturn.product_id)
                .filter(SaleReturn.party_id == party_id)
                .filter(SaleReturn.product_type_snapshot == "crops")
                .all()
            )

            for r in q:

                rows.append({
                    "dt": r[0],
                    "product": r[1],
                    "type": "sale_return",
                    "reference": f"SRET-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": "posted"
                })

        rows.sort(key=lambda x: x["dt"] or _dt.min, reverse=True)

        return rows[:limit]


    @staticmethod
    def list_fertilizer_activity(party_id: int, limit: int = 500):

        from database.models.purchase import Purchase
        from database.models.purchase_item import PurchaseItem
        from database.models.sale import Sale
        from database.models.sale_item import SaleItem
        from database.models.purchase_return import PurchaseReturn
        from database.models.sale_return import SaleReturn
        from database.models.product import Product

        rows = []

        with get_session() as s:

            # PURCHASE ITEMS
            q = (
                s.query(
                    Purchase.completed_at,
                    Product.name,
                    PurchaseItem.qty_bags,
                    PurchaseItem.total_weight_kg,
                    PurchaseItem.unit_cost_rate,
                    PurchaseItem.line_total,
                    Purchase.id,
                    Purchase.status
                )
                .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
                .join(Product, Product.id == PurchaseItem.product_id)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(PurchaseItem.product_type_snapshot == "fertilizer")
                .all()
            )

            for r in q:
                rows.append({
                    "dt": r[0],
                    "product_label": f"Fertilizer ({r[1]})",
                    "type": "purchase",
                    "reference": f"PUR-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": r[7]
                })

            # SALE ITEMS
            q = (
                s.query(
                    Sale.completed_at,
                    Product.name,
                    SaleItem.qty_bags,
                    SaleItem.total_weight_kg,
                    SaleItem.unit_sale_rate,
                    SaleItem.line_total,
                    Sale.id,
                    Sale.status
                )
                .join(Sale, Sale.id == SaleItem.sale_id)
                .join(Product, Product.id == SaleItem.product_id)
                .filter(Sale.buyer_party_id == party_id)
                .filter(SaleItem.product_type_snapshot == "fertilizer")
                .all()
            )

            for r in q:
                rows.append({
                    "dt": r[0],
                    "product_label": f"Fertilizer ({r[1]})",
                    "type": "sale",
                    "reference": f"SAL-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": r[7]
                })

            # PURCHASE RETURNS
            q = (
                s.query(
                    PurchaseReturn.created_at,
                    Product.name,
                    PurchaseReturn.qty_bags,
                    PurchaseReturn.qty_kg,
                    PurchaseReturn.unit_cost_snapshot,
                    PurchaseReturn.amount,
                    PurchaseReturn.id
                )
                .join(Product, Product.id == PurchaseReturn.product_id)
                .filter(PurchaseReturn.party_id == party_id)
                .filter(PurchaseReturn.product_type_snapshot == "fertilizer")
                .all()
            )

            for r in q:
                rows.append({
                    "dt": r[0],
                    "product_label": f"Fertilizer ({r[1]})",
                    "type": "purchase_return",
                    "reference": f"PRET-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": "posted"
                })

            # SALE RETURNS
            q = (
                s.query(
                    SaleReturn.created_at,
                    Product.name,
                    SaleReturn.qty_bags,
                    SaleReturn.qty_kg,
                    SaleReturn.unit_price_snapshot,
                    SaleReturn.amount,
                    SaleReturn.id
                )
                .join(Product, Product.id == SaleReturn.product_id)
                .filter(SaleReturn.party_id == party_id)
                .filter(SaleReturn.product_type_snapshot == "fertilizer")
                .all()
            )

            for r in q:
                rows.append({
                    "dt": r[0],
                    "product_label": f"Fertilizer ({r[1]})",
                    "type": "sale_return",
                    "reference": f"SRET-{r[6]}",
                    "bags": r[2],
                    "kg": r[3],
                    "unit_rate": r[4],
                    "amount": r[5],
                    "status": "posted"
                })

        rows.sort(key=lambda x: x["dt"] or _dt.min, reverse=True)

        return rows[:limit]
    

    @staticmethod
    def list_return_activity(party_id: int, limit: int = 500):

        from database.session import get_session
        from database.models.purchase_return import PurchaseReturn
        from database.models.sale_return import SaleReturn
        from database.models.product import Product
        from database.models.payment import Payment

        rows = []

        with get_session() as s:

            # -------------------------------
            # PURCHASE RETURNS (NOT YET SETTLED)
            # -------------------------------

            q = (
                s.query(
                    PurchaseReturn.created_at,
                    Product.name,
                    PurchaseReturn.product_type_snapshot,
                    PurchaseReturn.qty_bags,
                    PurchaseReturn.qty_kg,
                    PurchaseReturn.amount,
                    PurchaseReturn.reason,
                    PurchaseReturn.id,
                    PurchaseReturn.purchase_id,
                )
                .join(Product, Product.id == PurchaseReturn.product_id)
                .filter(PurchaseReturn.party_id == party_id)
                .all()
            )

            for r in q:

                ledger = "Crops" if r[2] == "crops" else "Fertilizer"
                return_id = r[7]
                purchase_id = r[8]

                # A return is settled if EITHER a matching cash reclaim
                # payment exists for it, OR nothing was ever paid in cash on
                # the original purchase in the first place (nothing to
                # reclaim - approve_purchase_return correctly no-ops in that
                # case, which is a legitimate "settled with zero cash"
                # outcome, not "pending"). The old version hardcoded
                # "pending" unconditionally and also looked for a payment
                # entity_type that never actually gets written anywhere -
                # meaning every return, forever, showed as pending even
                # after its refund had already landed.
                has_reclaim = s.query(Payment.id).filter(
                    Payment.entity_type == "purchase",
                    Payment.entity_id == purchase_id,
                    Payment.method == "refund",
                    Payment.notes.like(f"%RET-{return_id})%"),
                    Payment.status != "voided",
                ).first() is not None
                had_any_cash = s.query(Payment.id).filter(
                    Payment.entity_type == "purchase",
                    Payment.entity_id == purchase_id,
                    Payment.direction == "out",
                    Payment.method != "advance",
                    Payment.status != "voided",
                ).first() is not None
                status = "settled" if (has_reclaim or not had_any_cash) else "pending"

                rows.append({
                    "dt": r[0],
                    "ledger": f"{ledger} ({r[1]})",
                    "type": "purchase_return",
                    "reference": f"PRET-{return_id}",
                    "bags": r[3],
                    "kg": r[4],
                    "amount": r[5],
                    "reason": r[6],
                    "status": status
                })

            # -------------------------------
            # SALE RETURNS
            # -------------------------------

            q = (
                s.query(
                    SaleReturn.created_at,
                    Product.name,
                    SaleReturn.product_type_snapshot,
                    SaleReturn.qty_bags,
                    SaleReturn.qty_kg,
                    SaleReturn.amount,
                    SaleReturn.reason,
                    SaleReturn.id,
                    SaleReturn.sale_id,
                )
                .join(Product, Product.id == SaleReturn.product_id)
                .filter(SaleReturn.party_id == party_id)
                .all()
            )

            for r in q:

                ledger = "Crops" if r[2] == "crops" else "Fertilizer"
                return_id = r[7]
                sale_id = r[8]

                has_refund = s.query(Payment.id).filter(
                    Payment.entity_type == "sale",
                    Payment.entity_id == sale_id,
                    Payment.method == "refund",
                    Payment.notes.like(f"%RET-{return_id})%"),
                    Payment.status != "voided",
                ).first() is not None
                had_any_cash = s.query(Payment.id).filter(
                    Payment.entity_type == "sale",
                    Payment.entity_id == sale_id,
                    Payment.direction == "in",
                    Payment.method != "advance",
                    Payment.status != "voided",
                ).first() is not None
                status = "settled" if (has_refund or not had_any_cash) else "pending"

                rows.append({
                    "dt": r[0],
                    "ledger": f"{ledger} ({r[1]})",
                    "type": "sale_return",
                    "reference": f"SRET-{return_id}",
                    "bags": r[3],
                    "kg": r[4],
                    "amount": r[5],
                    "reason": r[6],
                    "status": status
                })

        rows.sort(key=lambda x: x["dt"] or _dt.min, reverse=True)

        return rows[:limit]
    


    @staticmethod
    def list_walkin_activity(walkin_name, limit: int = 500):
        """One walk-in's activity, laid out exactly like a registered customer's (documents with their payments,
        returns, settled markers), plus his weighings still waiting for the cashier."""
        from database.models.weigh_ticket import WeighTicket
        from database.session import get_session

        acts = PartyLedgerService.list_activity_details(
            WALKIN_PARTY_ID, limit=10 ** 6, walkin_name=walkin_name)

        # a purchase that came from a weighing says which one, so same-name walk-ins can be told apart
        pur_ids = [int(a["reference"].split("-")[-1]) for a in acts
                   if a.get("type") == "purchase" and str(a.get("reference", "")).startswith("PUR-")]
        with get_session() as s:
            tix = {int(pid): (tno or f"WT-{int(tid):05d}") for pid, tid, tno in
                   s.query(WeighTicket.purchase_id, WeighTicket.id, WeighTicket.ticket_no)
                   .filter(WeighTicket.purchase_id.in_(pur_ids or [0])).all()}
            pend = (s.query(WeighTicket).filter(WeighTicket.walkin_name == walkin_name)
                    .filter(WeighTicket.status.in_(("open", "submitted"))).all())
            pending = [{
                "dt": tk.created_at, "ledger": "crops", "type": "weighing",
                "reference": tk.ticket_no or f"WT-{int(tk.id):05d}", "summary": "Weighed - waiting for the cashier",
                "total": 0, "paid": None, "balance": None, "status": "pending",
            } for tk in pend]
        for a in acts:
            if a.get("type") == "purchase" and a.get("reference", "").startswith("PUR-"):
                tag = tix.get(int(a["reference"].split("-")[-1]))
                if tag:
                    a["summary"] = f"{walkin_name} ({tag})"
        acts += pending
        # waiting weighings are the newest things on his account; keep the rest in their grouped order
        acts.sort(key=lambda a: 0 if a.get("type") == "weighing" else 1)
        return acts[:limit]

    @staticmethod
    def list_walkin_crop_activity(walkin_name):

        from database.session import get_session
        from sqlalchemy import text

        with get_session() as s:

            rows = s.execute(text("""
                SELECT
                    s.completed_at AS dt,
                    p.name AS product,
                    'sale' AS type,
                    CONCAT('SAL-', s.id) AS reference,
                    si.qty_bags AS bags,
                    si.total_weight_kg AS kg,
                    si.unit_sale_rate AS unit_rate,
                    si.line_total AS amount,
                    s.status
                FROM sale_items si
                JOIN sales s ON s.id = si.sale_id
                JOIN products p ON p.id = si.product_id
                WHERE s.buyer_party_id = :wid
                AND s.walkin_name = :name
                AND si.product_type_snapshot = 'crops'

                UNION ALL

                SELECT
                    pch.completed_at,
                    p.name,
                    'purchase',
                    CONCAT('PUR-', pch.id),
                    pi.qty_bags,
                    pi.total_weight_kg,
                    pi.unit_cost_rate,
                    pi.line_total,
                    pch.status
                FROM purchase_items pi
                JOIN purchases pch ON pch.id = pi.purchase_id
                JOIN products p ON p.id = pi.product_id
                WHERE pch.supplier_party_id = :wid
                AND pch.walkin_name = :name
                AND pi.product_type_snapshot = 'crops'

                ORDER BY dt DESC
            """), {"name": walkin_name, "wid": WALKIN_PARTY_ID}).fetchall()

        return [dict(r._mapping) for r in rows]
    

    @staticmethod
    def list_walkin_fertilizer_activity(walkin_name):

        from database.session import get_session
        from sqlalchemy import text

        with get_session() as s:

            rows = s.execute(text("""
                SELECT
                    s.completed_at AS dt,
                    p.name AS product_label,
                    'sale' AS type,
                    CONCAT('SAL-', s.id) AS reference,
                    si.qty_bags AS bags,
                    0 AS kg,
                    si.unit_sale_rate AS unit_rate,
                    si.line_total AS amount,
                    s.status
                FROM sale_items si
                JOIN sales s ON s.id = si.sale_id
                JOIN products p ON p.id = si.product_id
                WHERE s.buyer_party_id = :wid
                AND s.walkin_name = :name
                AND si.product_type_snapshot = 'fertilizer'

                UNION ALL

                SELECT
                    pch.completed_at,
                    p.name,
                    'purchase',
                    CONCAT('PUR-', pch.id),
                    pi.qty_bags,
                    0,
                    pi.unit_cost_rate,
                    pi.line_total,
                    pch.status
                FROM purchase_items pi
                JOIN purchases pch ON pch.id = pi.purchase_id
                JOIN products p ON p.id = pi.product_id
                WHERE pch.supplier_party_id = :wid
                AND pch.walkin_name = :name
                AND pi.product_type_snapshot = 'fertilizer'

                ORDER BY dt DESC
            """), {"name": walkin_name, "wid": WALKIN_PARTY_ID}).fetchall()

        return [dict(r._mapping) for r in rows]
    

    @staticmethod
    def list_walkin_return_activity(walkin_name):

        from database.session import get_session
        from sqlalchemy import text

        with get_session() as s:

            rows = s.execute(text("""

                SELECT
                    pr.created_at AS dt,
                    pr.product_type_snapshot AS ledger,
                    'purchase_return' AS type,
                    CONCAT('PRET-', pr.id) AS reference,
                    pr.qty_bags AS bags,
                    pr.qty_kg AS kg,
                    pr.amount,
                    pr.reason,
                    'completed' AS status
                FROM purchase_returns pr
                JOIN purchases p ON p.id = pr.purchase_id
                WHERE p.supplier_party_id = :wid
                AND p.walkin_name = :name

                UNION ALL

                SELECT
                    sr.created_at,
                    sr.product_type_snapshot,
                    'sale_return',
                    CONCAT('SRET-', sr.id),
                    sr.qty_bags,
                    sr.qty_kg,
                    sr.amount,
                    sr.reason,
                    'completed'
                FROM sale_returns sr
                JOIN sales s ON s.id = sr.sale_id
                WHERE s.buyer_party_id = :wid
                AND s.walkin_name = :name

                ORDER BY dt DESC

            """), {"name": walkin_name, "wid": WALKIN_PARTY_ID}).fetchall()

        return [dict(r._mapping) for r in rows]