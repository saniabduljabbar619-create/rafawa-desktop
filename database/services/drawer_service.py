# database/services/drawer_service.py
# -*- coding: utf-8 -*-
"""
Drawer / operator-accountability derivation engine (Phase 1 - no UI).

Model (as agreed):
  * A user's profile tracks CASH THAT MOVED THROUGH THEIR HANDS, attributed to
    Payment.created_by_user_id (the settler), while the "Sales/Purchases/
    Advance IN/OUT" document cards are attributed to the document creator.
  * Real drawer cash = Payment rows with method != "advance"  PLUS  recorded
    money Advances. Advance-FUNDED payments (method == "advance") and
    AdvanceAllocation rows are paper (already counted when the advance was
    recorded) and are EXCLUDED from cash totals.  <- double-count guard
  * Cross-cashier settlement rule (one law covers sale/purchase/advance):
        settler != creator AND cash direction:
          direction == "out" -> CREATOR owes SETTLER
          direction == "in"  -> SETTLER owes CREATOR
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func, or_, and_, false, String
from sqlalchemy.exc import IntegrityError

from database.session import get_session
from database.models.payment import Payment
from database.models.advance import Advance
from database.models.sale import Sale
from database.models.purchase import Purchase
from database.models.sale_return import SaleReturn
from database.models.purchase_return import PurchaseReturn
from database.models.party import Party
from database.models.user import User
from database.models.cashier_settlement import CashierSettlement
from database.models.advance_allocations import AdvanceAllocation
from database.models.sale_item import SaleItem
from database.models.purchase_item import PurchaseItem
from database.models.sale_discount import SaleDiscount
from database.models.product import Product


def _f(x) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def _range_filter(stmt, col, start: Optional[date], end: Optional[date]):
    if start:
        stmt = stmt.where(col >= datetime.combine(start, time.min))
    if end:
        stmt = stmt.where(col <= datetime.combine(end, time.max))
    return stmt


def _outside_range_filter(stmt, col, start: Optional[date], end: Optional[date]):
    """
    Inverse of _range_filter - true when col falls OUTSIDE [start, end].
    Used to find a document whose OWN date does NOT fall in the selected
    range, even though a payment against it does - an "old settlement"
    physically collected today, against a purchase/sale from an earlier
    date. Without an unbounded range there's nothing to be "outside" of,
    so an unbounded call here deliberately matches nothing.
    """
    if not start and not end:
        return stmt.where(false())
    conditions = []
    if start:
        conditions.append(col < datetime.combine(start, time.min))
    if end:
        conditions.append(col > datetime.combine(end, time.max))
    return stmt.where(or_(*conditions))


def _purchase_adjustments(s, ids) -> Dict[int, float]:
    """Sum of price adjustments per purchase - an extra amount genuinely owed
    (see PartyLedgerService), so every payment-status calc must include it."""
    if not ids:
        return {}
    from database.models.purchase_price_adjustment import PurchasePriceAdjustment
    return {
        int(pid): _f(amt)
        for pid, amt in (
            s.query(PurchasePriceAdjustment.purchase_id,
                    func.coalesce(func.sum(PurchasePriceAdjustment.amount), 0.0))
            .filter(PurchasePriceAdjustment.purchase_id.in_(ids))
            .group_by(PurchasePriceAdjustment.purchase_id).all()
        )
    }


class DrawerService:

    # ------------------------------------------------------------------
    # Phase 2: persistent ledger sync (idempotent on source_payment_id)
    # ------------------------------------------------------------------
    @staticmethod
    def _sync_ledger(s, resolve) -> None:
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
    # main entry
    # ------------------------------------------------------------------
    @staticmethod
    def compute(user_id: int,
                start_date: Optional[date] = None,
                end_date: Optional[date] = None,
                table_start_date: Optional[date] = None,
                table_end_date: Optional[date] = None) -> Dict[str, Any]:
        """
        start_date/end_date scope the ACTIVITY CARDS (Split 1/2, cash_events,
        money_collected/paid_out) - the caller typically defaults these to
        TODAY so a fresh day starts at zero.

        table_start_date/table_end_date are INDEPENDENT and scope ONLY the
        cross-cashier settlement TABLE rows returned in
        cross_settlements["lines"] (the "show settlements other people did
        for me" view). Left None by default - the table shows its FULL
        history for browsing/auditing regardless of what the cards are
        scoped to - and only narrows when the caller explicitly supplies a
        range (the operator ticking "Filter by date" on screen).

        Reconciliation itself (i_owe/owed_to_me/verdicts) is NEVER affected
        by either of these - it is computed from the full, unbounded `lines`
        before any table-specific filtering is applied, so an open debt is
        never hidden by a date range meant only for browsing the log.

        Returns:
          {
            "cards": { ... the 10 cards + net_position ... },
            "cash_events":  [ per-payment rows handled by this user ],
            "cross_settlements": {
                "i_owe":    [ {other_user, amount} ],
                "owed_to_me":[ same shape ],
                "lines":    [ every cross line involving this user ],
            },
          }
        """
        with get_session() as s:
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            parties = {int(p.id): (p.display_name or f"Party#{p.id}")
                       for p in s.query(Party).all()}

            # -------- creator maps for documents (id -> creator, party, no)
            sale_meta = {int(r.id): (int(r.created_by_user_id or 0),
                                     int(r.buyer_party_id or 0),
                                     r.sale_no or f"SAL-{r.id}",
                                     r.walkin_name or "",
                                     r.created_at)
                         for r in s.query(Sale).all()}
            pur_meta = {int(r.id): (int(r.created_by_user_id or 0),
                                    int(r.supplier_party_id or 0),
                                    r.purchase_no or f"PUR-{r.id}",
                                    r.walkin_name or "",
                                    r.created_at)
                        for r in s.query(Purchase).all()}
            adv_meta = {int(r.id): (int(r.created_by_user_id or 0),
                                    int(r.party_id or 0),
                                    f"ADV-{r.id}", "",
                                    r.created_at)
                        for r in s.query(Advance).all()}

            # An advance's OWN direction (received/given) - NOT the settling
            # Payment's direction, which follows an inverted bookkeeping
            # convention for advances (settle_advance_payment deliberately
            # writes direction="out" for settling a RECEIVED advance). Using
            # payment.direction to classify an advance bucket silently swaps
            # advance_in and advance_out the moment a real settlement happens
            # - this is what needs to drive the bucket everywhere below.
            adv_direction = {int(r.id): str(r.direction or "")
                             for r in s.query(Advance).all()}

            def _creator_party_no(etype: str, eid: int):
                m = {"sale": sale_meta, "purchase": pur_meta, "advance": adv_meta}.get(etype, {})
                row = m.get(int(eid))
                if row is None:
                    return (0, 0, f"{etype.upper()}-{eid}", "")
                return row[:4]  # creator_id, party_id, doc_no, walkin - unchanged shape

            # A dedicated, minimal lookup for the document's own creation
            # date - reads the 5th element already present in the same
            # sale_meta/pur_meta/adv_meta dicts, without changing
            # _creator_party_no's own return shape (which has two other
            # call sites that don't need this). Used to flag whether a
            # cash event is settling something recorded on a DIFFERENT day
            # than today, so the UI can color-code "old settlement" rows
            # distinctly from genuinely same-day transactions.
            def _doc_created_at(etype: str, eid: int):
                m = {"sale": sale_meta, "purchase": pur_meta, "advance": adv_meta}.get(etype, {})
                row = m.get(int(eid))
                return row[4] if row else None

            def _is_old_settlement(etype: str, eid: int, payment_created_at) -> bool:
                doc_at = _doc_created_at(etype, eid)
                if doc_at is None or payment_created_at is None:
                    return False
                return doc_at.date() != payment_created_at.date()

            # Phase 2: keep the persistent ledger in step before we read it.
            DrawerService._sync_ledger(s, _creator_party_no)

            # ==========================================================
            # A. CASH EVENTS handled by this user (real cash only)
            #
            # Purchase and sale payments now split into two attributions:
            #
            # - A NORMAL payment (not a refund) is genuinely new cash that
            #   moved TODAY, whether it's the very first payment on a
            #   purchase from weeks ago, or one of several installments
            #   spread across different days - each one counts toward its
            #   OWN real date. An old, previously-unpaid purchase finally
            #   settled today should show up on TODAY's card; that's the
            #   client's own explicit ask.
            #
            # - A REFUND/reclaim (method="refund") still attributes to the
            #   ORIGINAL document's date, exactly as before: it's reversing
            #   a specific earlier payment, not a new event of its own, so
            #   it must land on the same day as what it's netting against -
            #   otherwise a return and its original payment would each show
            #   up as a half-story on two separate days, and neither would
            #   ever net to zero. This is the one thing that must NOT change.
            #
            # Advances are untouched here on purpose - every advance
            # Payment is always a settlement (the advance's own creation is
            # never a Payment at all, read straight from the Advance table),
            # so it's conceptually a discharge/reversal like a refund, not a
            # first payment on something new - it keeps the original,
            # document-date attribution.
            #
            # Four separate joined queries (two per document type) rather
            # than one query filtered in Python - keeps every date filter
            # as a real, indexed SQL join, avoiding a full-payments table
            # scan as the data grows.
            # ==========================================================
            _not_advance_method = func.lower(func.coalesce(Payment.method, "")) != "advance"
            _is_refund_method = func.lower(func.coalesce(Payment.method, "")) == "refund"
            _not_voided = Payment.status != "voided"

            pur_pay_stmt_normal = (
                select(Payment)
                .join(Purchase, Payment.entity_id == Purchase.id)
                .where(Payment.entity_type == "purchase")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(~_is_refund_method)
                .where(_not_voided)
            )
            pur_pay_stmt_normal = _range_filter(pur_pay_stmt_normal, Payment.created_at, start_date, end_date)

            pur_pay_stmt_refund = (
                select(Payment)
                .join(Purchase, Payment.entity_id == Purchase.id)
                .where(Payment.entity_type == "purchase")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(_is_refund_method)
                .where(_not_voided)
            )
            pur_pay_stmt_refund = _range_filter(pur_pay_stmt_refund, Purchase.created_at, start_date, end_date)

            sale_pay_stmt_normal = (
                select(Payment)
                .join(Sale, Payment.entity_id == Sale.id)
                .where(Payment.entity_type == "sale")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(~_is_refund_method)
                .where(_not_voided)
            )
            sale_pay_stmt_normal = _range_filter(sale_pay_stmt_normal, Payment.created_at, start_date, end_date)

            sale_pay_stmt_refund = (
                select(Payment)
                .join(Sale, Payment.entity_id == Sale.id)
                .where(Payment.entity_type == "sale")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(_is_refund_method)
                .where(_not_voided)
            )
            sale_pay_stmt_refund = _range_filter(sale_pay_stmt_refund, Sale.created_at, start_date, end_date)

            adv_pay_stmt_normal = (
                select(Payment)
                .join(Advance, Payment.entity_id == Advance.id)
                .where(Payment.entity_type == "advance")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(~_is_refund_method)
                .where(_not_voided)
            )
            adv_pay_stmt_normal = _range_filter(adv_pay_stmt_normal, Payment.created_at, start_date, end_date)

            adv_pay_stmt_refund = (
                select(Payment)
                .join(Advance, Payment.entity_id == Advance.id)
                .where(Payment.entity_type == "advance")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(_is_refund_method)
                .where(_not_voided)
            )
            adv_pay_stmt_refund = _range_filter(adv_pay_stmt_refund, Advance.created_at, start_date, end_date)

            my_payments = sorted(
                list(s.execute(pur_pay_stmt_normal).scalars().all())
                + list(s.execute(pur_pay_stmt_refund).scalars().all())
                + list(s.execute(sale_pay_stmt_normal).scalars().all())
                + list(s.execute(sale_pay_stmt_refund).scalars().all())
                + list(s.execute(adv_pay_stmt_normal).scalars().all())
                + list(s.execute(adv_pay_stmt_refund).scalars().all()),
                key=lambda p: p.created_at or datetime.min, reverse=True,
            )

            # ==========================================================
            # "Old settlements collected within this range" - the OPPOSITE
            # attribution from above: payments whose OWN timestamp falls in
            # the selected range, but whose ORIGINAL document does not (a
            # purchase/sale from an earlier date, paid off just now). These
            # never touch cash_events, the cards, or any card total - the
            # cards stay document-date-attributed so returns keep netting
            # correctly. This exists purely so the card detail popup can
            # show the admin real, physical cash movement that happened
            # today, even when it belongs to an older document - "the full
            # story" - without disturbing the balance figures at all.
            # ==========================================================
            # Restricted to refund-method payments only: a NORMAL payment is
            # now already surfaced correctly in cash_events via its own real
            # date (see the split above), so including it here too would
            # show the very same payment twice. Only a refund/reclaim still
            # attributes to the original document's date for the cards -
            # this is the one case still worth surfacing separately here.
            old_pur_stmt = (
                select(Payment, Purchase.created_at, Purchase.purchase_no)
                .join(Purchase, Payment.entity_id == Purchase.id)
                .where(Payment.entity_type == "purchase")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(_is_refund_method)
                .where(_not_voided)
            )
            old_pur_stmt = _range_filter(old_pur_stmt, Payment.created_at, start_date, end_date)
            old_pur_stmt = _outside_range_filter(old_pur_stmt, Purchase.created_at, start_date, end_date)

            old_sale_stmt = (
                select(Payment, Sale.created_at, Sale.sale_no)
                .join(Sale, Payment.entity_id == Sale.id)
                .where(Payment.entity_type == "sale")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(_is_refund_method)
                .where(_not_voided)
            )
            old_sale_stmt = _range_filter(old_sale_stmt, Payment.created_at, start_date, end_date)
            old_sale_stmt = _outside_range_filter(old_sale_stmt, Sale.created_at, start_date, end_date)

            old_adv_stmt = (
                select(Payment, Advance.created_at, Advance.id)
                .join(Advance, Payment.entity_id == Advance.id)
                .where(Payment.entity_type == "advance")
                .where(Payment.created_by_user_id == int(user_id))
                .where(_not_advance_method)
                .where(_is_refund_method)
                .where(_not_voided)
            )
            old_adv_stmt = _range_filter(old_adv_stmt, Payment.created_at, start_date, end_date)
            old_adv_stmt = _outside_range_filter(old_adv_stmt, Advance.created_at, start_date, end_date)

            old_settlement_rows = (
                [("purchase", row) for row in s.execute(old_pur_stmt).all()]
                + [("sale", row) for row in s.execute(old_sale_stmt).all()]
                + [("advance", row) for row in s.execute(old_adv_stmt).all()]
            )

            old_settlements: List[Dict[str, Any]] = []
            for et, row in old_settlement_rows:
                p, doc_created_at, doc_ref = row
                amt = _f(p.amount)
                creator_id, party_id, doc_no, walkin = _creator_party_no(et, p.entity_id)
                bucket = "other"
                if et == "sale":
                    bucket = "sale"
                elif et == "purchase":
                    bucket = "purchase"
                elif et == "advance":
                    # Every row reaching old_settlements for entity_type=
                    # "advance" is ALWAYS a settlement - creations never
                    # write a Payment at all (read directly from the
                    # Advance table instead, in the separate loop below).
                    #
                    # Always the OPPOSITE of the original advance's own
                    # direction, regardless of who created it or who's now
                    # settling it - a settlement is always the true,
                    # physical cash movement happening TODAY, on its own
                    # day, independent of the original creation. Discharging
                    # a received advance is genuinely money leaving the
                    # drawer right now (it may even be funded by an
                    # entirely different advance received in the meantime);
                    # being repaid on a given advance is genuinely money
                    # coming back right now. Netting a settlement against
                    # its own creator's earlier balance would hide that
                    # real, independent cash movement - the opposite of
                    # what a physical cash-drawer reconciliation needs.
                    if adv_direction.get(int(p.entity_id or 0)) == "received":
                        bucket = "advance_out"
                    else:
                        bucket = "advance_in"

                old_settlements.append({
                    "payment_id": int(p.id),
                    "when": p.created_at,
                    "entity_type": et,
                    "bucket": bucket,
                    "doc_no": doc_no,
                    "direction": (
                        # Always the TRUE physical direction of this
                        # settlement, independent of which card it groups
                        # under - discharging a received advance is always
                        # physically an outflow, being repaid on a given
                        # advance is always physically an inflow, regardless
                        # of whether it's grouped as advance_in or
                        # advance_out above for the same-person netting case.
                        ("out" if adv_direction.get(int(p.entity_id or 0)) == "received" else "in")
                        if et == "advance" else str(p.direction or "")
                    ),
                    "amount": amt,
                    "method": p.method or "",
                    "party": parties.get(party_id, walkin or ""),
                    "doc_creator": users.get(creator_id, f"User#{creator_id}"),
                    "doc_creator_id": creator_id,
                    "cross": bool(creator_id and creator_id != int(user_id)),
                    "notes": p.notes or "",
                    "doc_created_at": doc_created_at,
                })
            old_settlements.sort(key=lambda r: r["when"] or datetime.min, reverse=True)

            cash_events: List[Dict[str, Any]] = []
            paid_sales = paid_sales_refunds = 0.0
            paid_purch = paid_purch_reclaims = 0.0
            paid_adv_in = paid_adv_out = 0.0

            # Split 1 = documents I created AND personally settled myself.
            # Split 2 = documents someone ELSE created, that I settled.
            # Both are subsets of the SAME payment set above (all payments
            # settled by this user) - the grand totals (paid_sales etc.)
            # stay untouched and keep feeding the true cash-in-hand cards
            # (money_collected/money_paid_out) regardless of whose document
            # it was; these two new subsets are what the detail cards below
            # actually read.
            mine_sales = mine_sales_refunds = 0.0
            mine_purch = mine_purch_reclaims = 0.0
            mine_adv_in = mine_adv_out = 0.0

            split2_purch = split2_purch_reclaims = 0.0
            split2_sale = split2_sale_refunds = 0.0
            split2_adv_in = split2_adv_out = 0.0

            # ------------------------------------------------------------
            # SAME-DAY, SAME-CASHIER ADVANCE ROUND-TRIP - scoped strictly
            # to advances, nothing else touched. When the very same
            # cashier both records an advance AND settles it (fully or
            # partially) on the same calendar day, for the same party,
            # that portion never genuinely changed this cashier's net
            # cash position for the day - it arrived and left again,
            # same hands, same day. So it's suppressed from the drawer
            # entirely for that matching portion: the creation event's
            # own displayed amount is reduced by whatever was round-
            # tripped same-day, and the matching settlement(s) don't
            # appear as a separate line at all. Any genuine remainder -
            # a different day, a different cashier, or simply not yet
            # settled - still shows normally, exactly as before.
            adv_roundtrip_by_advance_id: Dict[int, float] = {}
            for p in my_payments:
                if str(p.entity_type or "") != "advance":
                    continue
                aid = int(p.entity_id or 0)
                creator_id, _party_id, _doc_no, _walkin = _creator_party_no("advance", aid)
                if creator_id != int(user_id):
                    continue
                adv_created_at = _doc_created_at("advance", aid)
                if adv_created_at is None or p.created_at is None:
                    continue
                if adv_created_at.date() != p.created_at.date():
                    continue
                adv_roundtrip_by_advance_id[aid] = adv_roundtrip_by_advance_id.get(aid, 0.0) + _f(p.amount)

            for p in my_payments:
                et = str(p.entity_type or "")
                amt = _f(p.amount)
                creator_id, party_id, doc_no, walkin = _creator_party_no(et, p.entity_id)

                if et == "advance":
                    _aid = int(p.entity_id or 0)
                    if creator_id == int(user_id):
                        _adv_created_at = _doc_created_at("advance", _aid)
                        if (_adv_created_at is not None and p.created_at is not None
                                and _adv_created_at.date() == p.created_at.date()):
                            # Same cashier, same day, same advance - this
                            # settlement is a genuine round-trip. Skip it
                            # entirely: no totals update, no cash_events
                            # row. The creation event below shows the
                            # reduced, net-remaining amount instead.
                            continue
                is_mine = (creator_id == int(user_id))
                bucket = "other"
                if et == "sale":
                    bucket = "sale"
                    if p.direction == "in":
                        paid_sales += amt
                        if is_mine:
                            mine_sales += amt
                        else:
                            split2_sale += amt
                    else:
                        paid_sales_refunds += amt
                        if is_mine:
                            mine_sales_refunds += amt
                        else:
                            split2_sale_refunds += amt
                elif et == "purchase":
                    bucket = "purchase"
                    if p.direction == "out":
                        paid_purch += amt
                        if is_mine:
                            mine_purch += amt
                        else:
                            split2_purch += amt
                    else:
                        paid_purch_reclaims += amt
                        if is_mine:
                            mine_purch_reclaims += amt
                        else:
                            split2_purch_reclaims += amt
                elif et == "advance":
                    # A Payment against entity_type="advance" is ALWAYS a
                    # SETTLEMENT (discharging/redeeming stored advance value)
                    # - the advance's own CREATION never writes a Payment at
                    # all (see the separate Advance-table loop below). So
                    # every row reaching here is a settlement, and settling a
                    # RECEIVED advance is the OPPOSITE cash direction from
                    # creating one: creation was money arriving; settlement
                    # is that stored value now being discharged/paid out -
                    # symmetric mirror logic for a GIVEN advance being repaid.
                    #
                    # This SUBTRACTS from the "Advance IN/OUT you Handled"
                    # card ONLY when the person being computed for is BOTH
                    # row_bucket (and paid_adv_in/paid_adv_out, which feed
                    # the main Money Collected / Money Paid Out cards)
                    # always follows the TRUE, physical cash direction of
                    # this settlement, regardless of who created the
                    # original advance or who's now settling it: a
                    # discharged Advance IN is genuinely money leaving the
                    # drawer right now (it may even be funded by an
                    # entirely different advance received in the
                    # meantime), and being repaid on a given advance is
                    # genuinely money coming back right now. Netting it
                    # against the creator's own earlier balance instead
                    # would hide that real, independent cash movement -
                    # the opposite of what reconciling actual cash on hand
                    # needs.
                    #
                    # mine_adv_in/mine_adv_out/split2_adv_in/split2_adv_out
                    # deliberately keep their EXISTING is_mine-based logic,
                    # unchanged - those feed a genuinely different metric
                    # ("Advance IN you handled" / Total Net IN-OUT), this
                    # user's own net involvement with advance activity, not
                    # literal cash-in-drawer-right-now. That's a separate
                    # concern from what changed here.
                    if adv_direction.get(int(p.entity_id or 0)) == "received":
                        bucket = "advance_in"
                        row_bucket = "advance_out"
                        paid_adv_out += amt
                        if is_mine:
                            mine_adv_in -= amt
                        else:
                            split2_adv_out += amt
                    else:
                        bucket = "advance_out"
                        row_bucket = "advance_in"
                        paid_adv_in += amt
                        if is_mine:
                            mine_adv_out -= amt
                        else:
                            split2_adv_in += amt

                cash_events.append({
                    "payment_id": int(p.id),
                    "when": p.created_at,
                    "entity_type": et,
                    "bucket": row_bucket if et == "advance" else bucket,
                    "doc_no": doc_no,
                    "direction": (
                        # For the same-person case, row_bucket still matches
                        # the ORIGINAL advance's type, so the true cash
                        # direction is its opposite (as before). For the
                        # cross-cashier case, row_bucket already IS the true
                        # direction, so it maps straight across instead.
                        ("out" if row_bucket == "advance_out" else "in")
                        if et == "advance" else str(p.direction or "")
                    ),
                    "amount": amt,
                    "method": p.method or "",
                    "party": parties.get(party_id, walkin or ""),
                    "doc_creator": users.get(creator_id, f"User#{creator_id}"),
                    "doc_creator_id": creator_id,
                    "cross": bool(creator_id and creator_id != int(user_id)),
                    "notes": p.notes or "",
                    # True when this settlement's own date differs from the
                    # original document's creation date - e.g. a sale
                    # recorded on the 15th, finally paid on the 25th. Lets
                    # the UI color-code it distinctly from a genuinely
                    # same-day transaction, so an operator can tell at a
                    # glance which rows are old settlements versus new ones.
                    "is_old_settlement": _is_old_settlement(et, p.entity_id, p.created_at),
                })

            # ----------------------------------------------------------------
            # A2. ADVANCE CREATION - a genuinely separate cash event, read
            # directly from Advance, NEVER from Payment.
            #
            # AdvanceService.record_advance_in/out writes ONLY an Advance row
            # (verified by reading the actual service - there is no Payment
            # written at creation). That is deliberate, not a bug elsewhere:
            # PartyBalanceService and AdvanceService._get_advance_remaining
            # both treat ANY Payment with entity_type=="advance" as a
            # SETTLEMENT that reduces the outstanding balance. If creation
            # also wrote a Payment, the advance would look instantly, fully
            # redeemed the moment it was created - breaking the customer
            # profile balance that already works correctly.
            #
            # So the drawer must read this event from its OWN source. It is
            # always "mine" (Split 1) - creating an advance is one atomic
            # action by one user, so there is no creator-vs-settler split
            # possible here the way there is for a sale or purchase payment.
            # Split 2 for advances still comes entirely from the SETTLEMENT
            # leg above (settle_advance_payment DOES write a Payment, and
            # that payment's creator/settler split already flows through
            # the loop above correctly).
            # ----------------------------------------------------------------
            adv_created_stmt = select(Advance).where(
                Advance.created_by_user_id == int(user_id))
            adv_created_stmt = _range_filter(
                adv_created_stmt, Advance.created_at, start_date, end_date)
            my_advances = s.execute(
                adv_created_stmt.order_by(Advance.created_at.desc())).scalars().all()

            for a in my_advances:
                raw_amt = _f(a.amount)
                roundtripped = adv_roundtrip_by_advance_id.get(int(a.id), 0.0)
                amt = max(raw_amt - roundtripped, 0.0)
                if amt <= 0.005:
                    # Fully round-tripped same-day, same-cashier - the
                    # creation never shows at all, matching its settlement
                    # (already skipped above) never showing either.
                    continue

                direction = str(a.direction or "").lower()
                if direction == "received":
                    bucket = "advance_in"
                    paid_adv_in += amt
                    mine_adv_in += amt
                    ev_direction = "in"
                else:
                    bucket = "advance_out"
                    paid_adv_out += amt
                    mine_adv_out += amt
                    ev_direction = "out"

                cash_events.append({
                    "payment_id": None,
                    "advance_id": int(a.id),
                    "when": a.created_at,
                    "entity_type": "advance",
                    "bucket": bucket,
                    "doc_no": f"ADV-{a.id}",
                    "direction": ev_direction,
                    "amount": amt,
                    "method": "advance",
                    "party": parties.get(int(a.party_id or 0), ""),
                    "doc_creator": users.get(int(user_id), f"User#{user_id}"),
                    "doc_creator_id": int(user_id),
                    "cross": False,
                    "notes": a.notes or "",
                    # A creation is never an old settlement - it's always a
                    # brand-new event on its own day. Present explicitly so
                    # every cash_events row carries this key consistently.
                    "is_old_settlement": False,
                })

            # net "paid" figures (cash in minus refunds back out, per bucket)
            # Split 1: documents I created AND settled myself - the mine-only
            # subset, NOT the grand total (that stays in paid_sales/paid_purch
            # for the honest top-line cash cards below).
            total_paid_sales = mine_sales - mine_sales_refunds
            total_paid_purchase = mine_purch - mine_purch_reclaims

            # Split 2: documents someone ELSE created, that I settled.
            purchase_settlement_total = round(split2_purch - split2_purch_reclaims, 2)
            sale_settlement_total = round(split2_sale - split2_sale_refunds, 2)
            advance_in_settlement_total = round(split2_adv_in, 2)
            advance_out_settlement_total = round(split2_adv_out, 2)
            total_settlement_in = round(sale_settlement_total + advance_in_settlement_total, 2)
            total_settlement_out = round(purchase_settlement_total + advance_out_settlement_total, 2)

            # ==========================================================
            # B. DOCUMENT CARDS (created by this user, net)
            # ==========================================================
            # Sales (completed) net of returns on those sales
            sale_stmt = select(Sale).where(Sale.created_by_user_id == int(user_id),
                                           Sale.status == "completed")
            sale_stmt = _range_filter(sale_stmt, Sale.created_at, start_date, end_date)
            my_sales = s.execute(sale_stmt).scalars().all()
            my_sale_ids = [int(x.id) for x in my_sales]
            sales_gross = sum(_f(x.total_amount) for x in my_sales)
            sales_returns = 0.0
            if my_sale_ids:
                sales_returns = _f(s.query(func.coalesce(func.sum(func.abs(SaleReturn.amount)), 0))
                                   .filter(SaleReturn.sale_id.in_(my_sale_ids)).scalar())
            sales_net = sales_gross - sales_returns

            pur_stmt = select(Purchase).where(Purchase.created_by_user_id == int(user_id),
                                              Purchase.status == "completed")
            pur_stmt = _range_filter(pur_stmt, Purchase.created_at, start_date, end_date)
            my_purs = s.execute(pur_stmt).scalars().all()
            my_pur_ids = [int(x.id) for x in my_purs]
            purch_gross = sum(_f(x.total_amount) for x in my_purs)
            purch_returns = 0.0
            if my_pur_ids:
                purch_returns = _f(s.query(func.coalesce(func.sum(func.abs(PurchaseReturn.amount)), 0))
                                    .filter(PurchaseReturn.purchase_id.in_(my_pur_ids)).scalar())
            purch_net = purch_gross - purch_returns

            # Advances recorded by user (money, completed), net of cash settlements
            adv_stmt = select(Advance).where(Advance.created_by_user_id == int(user_id),
                                             Advance.status == "completed",
                                             Advance.advance_type == "money")
            adv_stmt = _range_filter(adv_stmt, Advance.created_at, start_date, end_date)
            my_advs = s.execute(adv_stmt).scalars().all()
            adv_in_net = adv_out_net = 0.0
            for a in my_advs:
                amt = _f(a.amount)
                settled = _f(s.query(func.coalesce(func.sum(Payment.amount), 0))
                             .filter(Payment.entity_type == "advance",
                                     Payment.entity_id == int(a.id),
                                     Payment.status != "voided").scalar())
                net = max(amt - settled, 0.0)
                if a.direction == "received":
                    adv_in_net += net
                else:
                    adv_out_net += net

            agg_in = sales_net + adv_in_net
            agg_out = purch_net + adv_out_net

            # ==========================================================
            # C. CROSS-CASHIER SETTLEMENT LINES (both directions, global)
            #    One law: out -> creator owes settler; in -> settler owes creator
            # ==========================================================
            all_pay_stmt = select(Payment).where(
                func.lower(func.coalesce(Payment.method, "")) != "advance"
            ).where(Payment.status != "voided")
            # Deliberately NOT date-filtered, despite start_date/end_date being
            # available here - an open cross-cashier debt does not expire or
            # become invisible just because the activity cards above are
            # scoped to today. Reconciliation must reflect the TRUE, current
            # open/settled state regardless of what date range the rest of
            # the screen happens to be showing.
            all_payments = s.execute(all_pay_stmt).scalars().all()

            # adv_direction already built earlier (shared with section A's
            # cash_events classification) - reused here for _bucket_for.

            def _bucket_for(et: str, eid: int) -> str:
                if et == "purchase":
                    return "purchase"
                if et == "sale":
                    return "sale"
                if et == "advance":
                    return ("advance_in" if adv_direction.get(int(eid)) == "received"
                           else "advance_out")
                return "other"

            lines: List[Dict[str, Any]] = []
            for p in all_payments:
                settler = int(p.created_by_user_id or 0)
                et = str(p.entity_type or "")
                creator, party_id, doc_no, walkin = _creator_party_no(et, p.entity_id)
                if not creator or creator == settler:
                    continue
                if p.direction == "out":
                    debtor, creditor = creator, settler
                else:
                    debtor, creditor = settler, creator
                if int(user_id) not in (debtor, creditor):
                    continue
                lines.append({
                    "payment_id": int(p.id),
                    "when": p.created_at,
                    "entity_type": et,
                    "entity_id": int(p.entity_id or 0),
                    "bucket": _bucket_for(et, p.entity_id),
                    "doc_no": doc_no,
                    "amount": _f(p.amount),
                    "party": parties.get(party_id, walkin or ""),
                    "creator": users.get(creator, f"User#{creator}"),
                    "settler": users.get(settler, f"User#{settler}"),
                    "creator_id": creator,
                    "settler_id": settler,
                    "debtor_id": debtor,
                    "creditor_id": creditor,
                    "debtor": users.get(debtor, f"User#{debtor}"),
                    "creditor": users.get(creditor, f"User#{creditor}"),
                    "notes": p.notes or "",
                })

            # ---- overlay the persistent ledger onto the derived lines ----
            led = {}
            if lines:
                pids = [ln["payment_id"] for ln in lines]
                for r in s.execute(select(CashierSettlement).where(
                        CashierSettlement.source_payment_id.in_(pids))).scalars().all():
                    led[int(r.source_payment_id)] = r
            for ln in lines:
                r = led.get(ln["payment_id"])
                ln["settlement_id"] = int(r.id) if r is not None else None
                ln["status"] = (str(r.status) if r is not None else "open")
                ln["settled_at"] = (r.settled_at if r is not None else None)
                ln["settled_by"] = (users.get(int(r.settled_by_user_id), "")
                                    if (r is not None and r.settled_by_user_id) else "")

            # i_owe / owed_to_me stay over ALL lines: they feed net_position,
            # which is the TERMINAL position (see the note on settlement below).
            # The *_open dicts are the actionable subset that drives verdicts.
            i_owe: Dict[int, float] = {}
            owed_to_me: Dict[int, float] = {}
            open_i_owe: Dict[int, float] = {}
            open_owed_to_me: Dict[int, float] = {}
            for ln in lines:
                still_open = (ln["status"] != "settled")
                if ln["debtor_id"] == int(user_id):
                    i_owe[ln["creditor_id"]] = i_owe.get(ln["creditor_id"], 0.0) + ln["amount"]
                    if still_open:
                        open_i_owe[ln["creditor_id"]] = open_i_owe.get(ln["creditor_id"], 0.0) + ln["amount"]
                else:
                    owed_to_me[ln["debtor_id"]] = owed_to_me.get(ln["debtor_id"], 0.0) + ln["amount"]
                    if still_open:
                        open_owed_to_me[ln["debtor_id"]] = open_owed_to_me.get(ln["debtor_id"], 0.0) + ln["amount"]

            def _bucketize(d):
                return [{"other_user_id": uid,
                         "other_user": users.get(uid, f"User#{uid}"),
                         "amount": round(amt, 2)}
                        for uid, amt in sorted(d.items(), key=lambda kv: -kv[1])]

            total_i_owe = round(sum(i_owe.values()), 2)
            total_owed_to_me = round(sum(owed_to_me.values()), 2)
            open_i_owe_total = round(sum(open_i_owe.values()), 2)
            open_owed_to_me_total = round(sum(open_owed_to_me.values()), 2)

            # Cash that ALREADY changed hands between drawers. It writes no
            # Payment row, so it can never reach cash_handled_net - but it is
            # real money that physically moved and MUST be shown, or the
            # headline cards will not add up to net_position.
            settled_i_owe = round(total_i_owe - open_i_owe_total, 2)
            settled_owed_to_me = round(total_owed_to_me - open_owed_to_me_total, 2)
            settled_net = round(settled_owed_to_me - settled_i_owe, 2)

            # --- Net Position on a CASH-HANDLED basis (the drawer truth) -----
            # Money that physically entered vs left this user's hands, then
            # adjusted by unsettled inter-cashier balances.
            # Each side nets its OWN reversals now, rather than a reclaim/
            # refund crossing over to the opposite card. Mathematically this
            # doesn't change cash_handled_net (money_collected - money_paid_out
            # is identical either way - same total, different internal split)
            # but it does mean a FULLY reversed purchase now correctly shows
            # money_paid_out=0 on its own, not just net_position=0 - matching
            # the client's explicit ask: every card cleared, "as if no
            # purchase was ever made," not only the difference between them.
            money_collected = round(paid_sales - paid_sales_refunds + paid_adv_in, 2)
            money_paid_out = round(paid_purch - paid_purch_reclaims + paid_adv_out, 2)
            cash_handled_net = round(money_collected - money_paid_out, 2)
            net_position = round(cash_handled_net + total_owed_to_me - total_i_owe, 2)

            # --- Plain-language layer (built for non-literate operators) ------
            # Per-cashier NET instruction using physical verbs (give / collect),
            # never "owe/owed". English + Hausa, ready for the screen to paint.
            def _amt(v):
                return f"\u20a6{v:,.0f}"

            verdicts: List[Dict[str, Any]] = []
            for other_id in (set(open_owed_to_me) | set(open_i_owe)):
                net = round(open_owed_to_me.get(other_id, 0.0) - open_i_owe.get(other_id, 0.0), 2)
                if abs(net) < 0.01:
                    continue
                name = users.get(other_id, f"User#{other_id}")
                if net > 0:  # they hold your money -> go collect it
                    verdicts.append({
                        "other_user_id": other_id, "other_user": name,
                        "action": "collect", "amount": round(net, 2),
                        "en": f"Collect {_amt(net)} from {name}",
                        "ha": f"Ka kar\u0253i {_amt(net)} daga {name}",
                    })
                else:  # you hold their money -> hand it over
                    give = round(-net, 2)
                    verdicts.append({
                        "other_user_id": other_id, "other_user": name,
                        "action": "give", "amount": give,
                        "en": f"Give {name} {_amt(give)}",
                        "ha": f"Ka ba {name} {_amt(give)}",
                    })
            verdicts.sort(key=lambda v: -v["amount"])

            is_balanced = (len(verdicts) == 0)
            if is_balanced:
                status_en = "Everything is balanced"
                status_ha = "Komai ya daidaita"
            else:
                status_en = "You have money to settle with other cashiers"
                status_ha = "Kana da ku\u0257i da za ka daidaita da sauran ma'aikata"

            # A minus sign is accountant notation, and red + minus reads as
            # "you did wrong" - but a negative net is a cashier doing his job
            # (he authorised money out). So: the LABEL carries the direction,
            # the number is always plain. Same law as the verdicts.
            net_abs = round(abs(net_position), 2)
            if net_position > 0.005:
                net_dir = "holding"
                net_en = "Money that should be in your hands"
                net_ha = "Ku\u0257in da ya kamata ya kasance a hannunka"
            elif net_position < -0.005:
                net_dir = "released"
                net_en = "Money that went out through you"
                net_ha = "Ku\u0257in da ya fita ta hannunka"
            else:
                net_dir = "clear"
                net_en = "Your hands are clear"
                net_ha = "Hannunka ba shi da ku\u0257i"

            if settled_net > 0.005:
                settled_en = "Money you received from other cashiers"
                settled_ha = "Ku\u0257in da ka kar\u0253a daga sauran ma'aikata"
            elif settled_net < -0.005:
                settled_en = "Money you gave other cashiers"
                settled_ha = "Ku\u0257in da ka ba sauran ma'aikata"
            else:
                settled_en = "Settled with other cashiers"
                settled_ha = "An daidaita da sauran ma'aikata"

            if table_start_date or table_end_date:
                _lo = (datetime.combine(table_start_date, time.min)
                      if table_start_date else None)
                _hi = (datetime.combine(table_end_date, time.max)
                      if table_end_date else None)
                lines_for_table = [
                    ln for ln in lines
                    if ln.get("when")
                    and (not _lo or ln["when"] >= _lo)
                    and (not _hi or ln["when"] <= _hi)
                ]
            else:
                lines_for_table = lines

            return {
                "user": users.get(int(user_id), f"User#{user_id}"),
                "cards": {
                    "total_paid_sales": round(total_paid_sales, 2),
                    "total_paid_purchase": round(total_paid_purchase, 2),
                    "total_paid_advance_in": round(mine_adv_in, 2),
                    "total_paid_advance_out": round(mine_adv_out, 2),
                    # Split 1 totals - Sales + Advance IN handled / Purchase +
                    # Advance OUT handled, ALL mine-only (creator == settler
                    # == me), consistent with the four detail cards above.
                    "total_net_in": round(total_paid_sales + mine_adv_in, 2),
                    "total_net_out": round(total_paid_purchase + mine_adv_out, 2),
                    "sales_net": round(sales_net, 2),
                    "purchases_net": round(purch_net, 2),
                    "advance_in_net": round(adv_in_net, 2),
                    "advance_out_net": round(adv_out_net, 2),
                    "aggregated_in": round(agg_in, 2),
                    "aggregated_out": round(agg_out, 2),
                    "i_owe_total": total_i_owe,
                    "owed_to_me_total": total_owed_to_me,
                    "i_owe_open": open_i_owe_total,
                    "owed_to_me_open": open_owed_to_me_total,
                    "settled_with_cashiers": settled_net,
                    # Split 2 - documents someone ELSE created, that I
                    # personally settled. Plain sums (not a net/reconciliation
                    # figure - see the note above where these are computed),
                    # broken down by which document type it was.
                    "purchase_settlement_total": purchase_settlement_total,
                    "sale_settlement_total": sale_settlement_total,
                    "advance_in_settlement_total": advance_in_settlement_total,
                    "advance_out_settlement_total": advance_out_settlement_total,
                    "total_settlement_in": total_settlement_in,
                    "total_settlement_out": total_settlement_out,
                    "net_position": net_position,
                },
                "human": {
                    "money_collected": money_collected,
                    "money_paid_out": money_paid_out,
                    "settled_with_cashiers": settled_net,
                    "cash_handled_net": cash_handled_net,
                    "net_position": net_position,
                    "net_position_abs": net_abs,
                    "net_direction": net_dir,
                    "is_balanced": is_balanced,
                    "status_en": status_en,
                    "status_ha": status_ha,
                    "verdicts": verdicts,
                    "labels": {
                        "money_collected": {"en": "Money you collected", "ha": "Ku\u0257in da ka tara"},
                        "money_paid_out": {"en": "Money you paid out", "ha": "Ku\u0257in da ka fitar"},
                        "settled_with_cashiers": {"en": settled_en, "ha": settled_ha},
                        "net_position": {"en": net_en, "ha": net_ha},
                    },
                },
                # The TABLE view is independently filterable, decoupled from
                # both the (today-scoped) activity cards and the (always
                # unbounded) reconciliation this same `lines` list already
                # fed above. Default (no table range given) = full history,
                # matching "let the rows be, but filterable" - only narrows
                # when the caller explicitly supplies table_start_date/end.
                "cash_events": sorted(
                    cash_events, key=lambda r: r["when"] or datetime.min, reverse=True),
                "old_settlements": old_settlements,
                "cross_settlements": {
                    "i_owe": _bucketize(i_owe),
                    "owed_to_me": _bucketize(owed_to_me),
                    "i_owe_open": _bucketize(open_i_owe),
                    "owed_to_me_open": _bucketize(open_owed_to_me),
                    "lines": lines_for_table,
                },
            }

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
            return {"ok": True, "settled": n}

    # ======================================================================
    # PARTY TOTALS - "who has this cashier sold to / bought from, and how
    # much" - grouped by party, rather than the drawer's own flat, per-
    # transaction cash_events list. Defaults to this user's own transactions
    # (created_by_user_id), matching the drawer's own established scoping;
    # scope="all" widens it to every transaction system-wide, across every
    # cashier/admin, for when the admin wants the full business picture
    # rather than just their own shift.
    # ======================================================================
    @staticmethod
    def party_totals(user_id: int, scope: str = "mine",
                     start_date: Optional[date] = None,
                     end_date: Optional[date] = None) -> Dict[str, Any]:
        scope = (scope or "mine").strip().lower()
        with get_session() as s:
            sale_stmt = select(Sale).where(Sale.status != "voided")
            pur_stmt = select(Purchase).where(Purchase.status != "voided")
            if scope != "all":
                sale_stmt = sale_stmt.where(Sale.created_by_user_id == int(user_id))
                pur_stmt = pur_stmt.where(Purchase.created_by_user_id == int(user_id))
            sale_stmt = _range_filter(sale_stmt, Sale.created_at, start_date, end_date)
            pur_stmt = _range_filter(pur_stmt, Purchase.created_at, start_date, end_date)

            sales = s.execute(sale_stmt).scalars().all()
            purchases = s.execute(pur_stmt).scalars().all()

            parties = {int(p.id): (p.display_name or f"Party#{p.id}")
                       for p in s.query(Party).all()}
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}

            by_party: Dict[int, Dict[str, Any]] = {}

            def _bucket(party_id: int, name: str):
                if party_id not in by_party:
                    by_party[party_id] = {
                        "party_id": party_id, "party_name": name,
                        "sales_count": 0, "sales_total": 0.0,
                        "purchases_count": 0, "purchases_total": 0.0,
                    }
                return by_party[party_id]

            for sale in sales:
                pid = int(sale.buyer_party_id or 0)
                name = parties.get(pid, sale.walkin_name or f"Party#{pid}")
                b = _bucket(pid, name)
                b["sales_count"] += 1
                b["sales_total"] += _f(sale.total_amount)

            for pur in purchases:
                pid = int(pur.supplier_party_id or 0)
                name = parties.get(pid, pur.walkin_name or f"Party#{pid}")
                b = _bucket(pid, name)
                b["purchases_count"] += 1
                b["purchases_total"] += _f(pur.total_amount)

            rows = sorted(
                by_party.values(),
                key=lambda r: (r["sales_total"] + r["purchases_total"]),
                reverse=True,
            )

            return {
                "scope": scope,
                "rows": rows,
                "grand_sales_total": sum(r["sales_total"] for r in rows),
                "grand_purchases_total": sum(r["purchases_total"] for r in rows),
            }

    @staticmethod
    def party_transactions(party_id: int, user_id: int, scope: str = "mine",
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> List[Dict[str, Any]]:
        """
        The individual sales and purchases behind one party's row in
        party_totals - what the drill-down (double-click) opens onto.
        Same scoping rules as party_totals, filtered to this one party.

        Each row also carries:
          payment_status  - "paid" / "partial" / "unpaid", computed the
                             same, correct way as everywhere else in this
                             app: real cash payments PLUS genuine advance
                             allocations (which never create a Payment row
                             at all - see the earlier advance-settlement
                             fix this session) against the net total
                             (after returns, and after any manual discount
                             for sales).
          product_summary - "Type (Product Name)" for a single-product
                             transaction, or "Product A, Product B +N more"
                             for a multi-line one - built from a single
                             batch query per document type, not one query
                             per row.
        """
        scope = (scope or "mine").strip().lower()
        party_id = int(party_id or 0)
        with get_session() as s:
            sale_stmt = select(Sale).where(Sale.buyer_party_id == party_id)
            pur_stmt = select(Purchase).where(Purchase.supplier_party_id == party_id)
            if scope != "all":
                sale_stmt = sale_stmt.where(Sale.created_by_user_id == int(user_id))
                pur_stmt = pur_stmt.where(Purchase.created_by_user_id == int(user_id))
            sale_stmt = _range_filter(sale_stmt, Sale.created_at, start_date, end_date)
            pur_stmt = _range_filter(pur_stmt, Purchase.created_at, start_date, end_date)

            sales = s.execute(sale_stmt).scalars().all()
            purchases = s.execute(pur_stmt).scalars().all()
            sale_ids = [int(sale.id) for sale in sales]
            pur_ids = [int(pur.id) for pur in purchases]

            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = {int(p.id): (p.name or f"Product#{p.id}", p.product_type or "")
                        for p in s.query(Product).all()}

            # ---- product summaries, one batch query per document type ----
            sale_lines: Dict[int, List[int]] = {}
            if sale_ids:
                for si in (s.query(SaleItem)
                          .filter(SaleItem.sale_id.in_(sale_ids)).all()):
                    sale_lines.setdefault(int(si.sale_id), []).append(int(si.product_id))

            pur_lines: Dict[int, List[int]] = {}
            if pur_ids:
                for pi in (s.query(PurchaseItem)
                          .filter(PurchaseItem.purchase_id.in_(pur_ids)).all()):
                    pur_lines.setdefault(int(pi.purchase_id), []).append(int(pi.product_id))

            def _product_summary(product_ids: List[int]) -> str:
                if not product_ids:
                    return ""
                names = [products.get(pid, (f"Product#{pid}", ""))[0] for pid in product_ids]
                types = [products.get(pid, ("", ""))[1] for pid in product_ids]
                if len(names) == 1:
                    t = types[0].capitalize() if types[0] else ""
                    return f"{t} ({names[0]})" if t else names[0]
                extra = len(names) - 1
                return f"{names[0]} +{extra} more" if extra > 0 else names[0]

            # ---- payment status, batch queries for the whole set ----
            def _paid_and_alloc(entity_type: str, ids: List[int]):
                if not ids:
                    return {}, {}
                paid_rows = (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0.0))
                    .filter(Payment.entity_type == entity_type, Payment.entity_id.in_(ids))
                    # Only the settling direction - a refund (opposite
                    # direction) must not count as extra money paid.
                    .filter(Payment.direction == ("in" if entity_type == "sale" else "out"))
                    .filter(Payment.method != "advance")
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                )
                alloc_rows = (
                    s.query(AdvanceAllocation.entity_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
                    .filter(AdvanceAllocation.entity_type == entity_type, AdvanceAllocation.entity_id.in_(ids))
                    .group_by(AdvanceAllocation.entity_id).all()
                )
                return ({int(eid): _f(amt) for eid, amt in paid_rows},
                       {int(eid): _f(amt) for eid, amt in alloc_rows})

            sale_paid, sale_alloc = _paid_and_alloc("sale", sale_ids)
            pur_paid, pur_alloc = _paid_and_alloc("purchase", pur_ids)
            pur_adj = _purchase_adjustments(s, pur_ids)

            sale_returned: Dict[int, float] = {}
            if sale_ids:
                for eid, amt in (s.query(SaleReturn.sale_id, func.coalesce(func.sum(SaleReturn.amount), 0.0))
                                 .filter(SaleReturn.sale_id.in_(sale_ids))
                                 .group_by(SaleReturn.sale_id).all()):
                    sale_returned[int(eid)] = abs(_f(amt))

            pur_returned: Dict[int, float] = {}
            if pur_ids:
                for eid, amt in (s.query(PurchaseReturn.purchase_id, func.coalesce(func.sum(PurchaseReturn.amount), 0.0))
                                 .filter(PurchaseReturn.purchase_id.in_(pur_ids))
                                 .group_by(PurchaseReturn.purchase_id).all()):
                    pur_returned[int(eid)] = abs(_f(amt))

            sale_discounts: Dict[int, float] = {}
            if sale_ids:
                for eid, amt in (s.query(SaleDiscount.sale_id, func.coalesce(func.sum(SaleDiscount.amount), 0.0))
                                 .filter(SaleDiscount.sale_id.in_(sale_ids))
                                 .group_by(SaleDiscount.sale_id).all()):
                    sale_discounts[int(eid)] = _f(amt)

            def _status(net_total: float, settled: float) -> str:
                if net_total <= 0.005:
                    return "paid"
                if settled >= net_total - 0.01:
                    return "paid"
                if settled > 0.005:
                    return "partial"
                return "unpaid"

            rows: List[Dict[str, Any]] = []
            for sale in sales:
                sid = int(sale.id)
                net_total = max(_f(sale.total_amount) - sale_returned.get(sid, 0.0) - sale_discounts.get(sid, 0.0), 0.0)
                settled = sale_paid.get(sid, 0.0) + sale_alloc.get(sid, 0.0)
                rows.append({
                    "kind": "sale",
                    "id": sid,
                    "doc_no": sale.sale_no or f"SAL-{sale.id}",
                    "when": sale.created_at,
                    "amount": _f(sale.total_amount),
                    "created_by": users.get(int(sale.created_by_user_id or 0), ""),
                    "notes": sale.notes or "",
                    "payment_status": "voided" if sale.status == "voided" else _status(net_total, settled),
                    "product_summary": _product_summary(sale_lines.get(sid, [])),
                })
            for pur in purchases:
                pid = int(pur.id)
                net_total = max(_f(pur.total_amount) - pur_returned.get(pid, 0.0) + pur_adj.get(pid, 0.0), 0.0)
                settled = pur_paid.get(pid, 0.0) + pur_alloc.get(pid, 0.0)
                rows.append({
                    "kind": "purchase",
                    "id": pid,
                    "doc_no": pur.purchase_no or f"PUR-{pur.id}",
                    "when": pur.created_at,
                    "amount": _f(pur.total_amount),
                    "created_by": users.get(int(pur.created_by_user_id or 0), ""),
                    "notes": pur.notes or "",
                    "payment_status": "voided" if pur.status == "voided" else _status(net_total, settled),
                    "product_summary": _product_summary(pur_lines.get(pid, [])),
                })

            rows.sort(key=lambda r: r["when"] or datetime.min, reverse=True)
            return rows

    # ======================================================================
    # CASHIER BUSINESS HISTORY - every sale (or every purchase) created by
    # ONE cashier, individually, across ALL parties. Same batched-query
    # technique as party_transactions above (one grouped query per
    # payment/allocation/return/discount total, not one query per row), but
    # scoped the opposite way: one user across every party, restricted to a
    # single document kind at a time to match the screen's Purchases/Sales
    # dropdown, and each row carries its own party name since (unlike
    # party_transactions) the party isn't fixed for the whole call.
    # ======================================================================
    @staticmethod
    def cashier_transactions(user_id: int, kind: str,
                             start_date: Optional[date] = None,
                             end_date: Optional[date] = None) -> List[Dict[str, Any]]:
        kind = (kind or "").strip().lower()
        if kind not in ("sale", "purchase", "return"):
            raise ValueError("kind must be 'sale', 'purchase' or 'return'")
        user_id = int(user_id or 0)

        if kind == "return":
            return DrawerService._cashier_returns(user_id, start_date, end_date)

        with get_session() as s:
            if kind == "sale":
                stmt = select(Sale).where(Sale.created_by_user_id == user_id)
                stmt = _range_filter(stmt, Sale.created_at, start_date, end_date)
                docs = s.execute(stmt).scalars().all()
            else:
                stmt = select(Purchase).where(Purchase.created_by_user_id == user_id)
                stmt = _range_filter(stmt, Purchase.created_at, start_date, end_date)
                docs = s.execute(stmt).scalars().all()

            doc_ids = [int(d.id) for d in docs]

            parties = {int(p.id): (p.display_name or f"Party#{p.id}")
                       for p in s.query(Party).all()}
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = {int(p.id): (p.name or f"Product#{p.id}", p.product_type or "")
                        for p in s.query(Product).all()}

            # ---- product summaries, one batch query for the whole set ----
            item_lines: Dict[int, List[int]] = {}
            if doc_ids:
                if kind == "sale":
                    for si in (s.query(SaleItem)
                              .filter(SaleItem.sale_id.in_(doc_ids)).all()):
                        item_lines.setdefault(int(si.sale_id), []).append(int(si.product_id))
                else:
                    for pi in (s.query(PurchaseItem)
                              .filter(PurchaseItem.purchase_id.in_(doc_ids)).all()):
                        item_lines.setdefault(int(pi.purchase_id), []).append(int(pi.product_id))

            def _product_summary(product_ids: List[int]) -> str:
                if not product_ids:
                    return ""
                names = [products.get(pid, (f"Product#{pid}", ""))[0] for pid in product_ids]
                types = [products.get(pid, ("", ""))[1] for pid in product_ids]
                if len(names) == 1:
                    t = types[0].capitalize() if types[0] else ""
                    return f"{t} ({names[0]})" if t else names[0]
                extra = len(names) - 1
                return f"{names[0]} +{extra} more" if extra > 0 else names[0]

            # ---- payment status, batch queries for the whole set ----
            paid_map: Dict[int, float] = {}
            alloc_map: Dict[int, float] = {}
            return_map: Dict[int, float] = {}
            discount_map: Dict[int, float] = {}

            if doc_ids:
                pay_direction = "in" if kind == "sale" else "out"
                paid_rows = (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0.0))
                    .filter(Payment.entity_type == kind, Payment.entity_id.in_(doc_ids))
                    .filter(Payment.direction == pay_direction)
                    .filter(Payment.method != "advance")
                    .filter(~Payment.notes.like("%Advance settlement%"))
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                )
                paid_map = {int(eid): _f(amt) for eid, amt in paid_rows}

                alloc_rows = (
                    s.query(AdvanceAllocation.entity_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
                    .filter(AdvanceAllocation.entity_type == kind, AdvanceAllocation.entity_id.in_(doc_ids))
                    .group_by(AdvanceAllocation.entity_id).all()
                )
                alloc_map = {int(eid): _f(amt) for eid, amt in alloc_rows}

                if kind == "sale":
                    for eid, amt in (s.query(SaleReturn.sale_id, func.coalesce(func.sum(SaleReturn.amount), 0.0))
                                     .filter(SaleReturn.sale_id.in_(doc_ids))
                                     .group_by(SaleReturn.sale_id).all()):
                        return_map[int(eid)] = abs(_f(amt))
                    for eid, amt in (s.query(SaleDiscount.sale_id, func.coalesce(func.sum(SaleDiscount.amount), 0.0))
                                     .filter(SaleDiscount.sale_id.in_(doc_ids))
                                     .group_by(SaleDiscount.sale_id).all()):
                        discount_map[int(eid)] = _f(amt)
                else:
                    for eid, amt in (s.query(PurchaseReturn.purchase_id, func.coalesce(func.sum(PurchaseReturn.amount), 0.0))
                                     .filter(PurchaseReturn.purchase_id.in_(doc_ids))
                                     .group_by(PurchaseReturn.purchase_id).all()):
                        return_map[int(eid)] = abs(_f(amt))

            adj_map = _purchase_adjustments(s, doc_ids) if kind == "purchase" else {}

            def _status(net_total: float, settled: float) -> str:
                if net_total <= 0.005:
                    return "paid"
                if settled >= net_total - 0.01:
                    return "paid"
                if settled > 0.005:
                    return "partial"
                return "unpaid"

            rows: List[Dict[str, Any]] = []
            for d in docs:
                did = int(d.id)
                if kind == "sale":
                    party_id = int(d.buyer_party_id or 0)
                    doc_no = d.sale_no or f"SAL-{d.id}"
                else:
                    party_id = int(d.supplier_party_id or 0)
                    doc_no = d.purchase_no or f"PUR-{d.id}"

                party_name = parties.get(party_id, d.walkin_name or f"Party#{party_id}")
                raw_total = _f(d.total_amount)
                net_total = max(raw_total - return_map.get(did, 0.0) - discount_map.get(did, 0.0) + adj_map.get(did, 0.0), 0.0)
                settled = paid_map.get(did, 0.0) + alloc_map.get(did, 0.0)

                rows.append({
                    "kind": kind,
                    "id": did,
                    "doc_no": doc_no,
                    "when": d.created_at,
                    "party_name": party_name,
                    "amount": raw_total,
                    "created_by": users.get(int(d.created_by_user_id or 0), ""),
                    "payment_status": "voided" if d.status == "voided" else _status(net_total, settled),
                    "product_summary": _product_summary(item_lines.get(did, [])),
                })

            rows.sort(key=lambda r: r["when"] or datetime.min, reverse=True)
            return rows

    # ------------------------------------------------------------------
    # "Total Returns" - the third cashier_transactions() kind. Unifies
    # SaleReturn + PurchaseReturn rows created by this user into one list,
    # the same way _KIND_COLORS in the dialog anticipated a future "Both"
    # option mixing sale/purchase rows together.
    #
    # Each return is already scoped to a single product/item (unlike Sale/
    # Purchase, which can hold many lines), so - unlike the product_summary
    # above - this shows the exact quantity returned, not just a name.
    #
    # "id" on each row is the PARENT sale/purchase id, not the return's own
    # id: there is no standalone return-detail dialog today, so double-
    # clicking a return opens the full sale/purchase history it came from
    # (SaleHistoryDialog / PurchaseHistoryDialog) - the same target
    # AdminReturnsView already uses for its own row preview.
    #
    # There's no payment_status on either return model (no paid/partial/
    # unpaid concept for a return), so that dict key carries the return's
    # `reason` instead. The dialog's existing status rendering already
    # falls back to plain, uncolored text for any value it doesn't
    # recognize, and skips the row-tint entirely - so this needed no
    # changes on the UI side.
    # ------------------------------------------------------------------
    @staticmethod
    def _cashier_returns(user_id: int,
                          start_date: Optional[date],
                          end_date: Optional[date]) -> List[Dict[str, Any]]:
        with get_session() as s:
            sr_stmt = select(SaleReturn).where(SaleReturn.created_by_user_id == user_id)
            sr_stmt = _range_filter(sr_stmt, SaleReturn.created_at, start_date, end_date)
            sale_returns = s.execute(sr_stmt).scalars().all()

            pr_stmt = select(PurchaseReturn).where(PurchaseReturn.created_by_user_id == user_id)
            pr_stmt = _range_filter(pr_stmt, PurchaseReturn.created_at, start_date, end_date)
            purchase_returns = s.execute(pr_stmt).scalars().all()

            if not sale_returns and not purchase_returns:
                return []

            parties = {int(p.id): (p.display_name or f"Party#{p.id}")
                       for p in s.query(Party).all()}
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = {int(p.id): (p.name or f"Product#{p.id}")
                        for p in s.query(Product).all()}

            def _qty_summary(product_name: str, type_snapshot: str,
                              qty_kg: float, qty_bags: int) -> str:
                t = (type_snapshot or "").capitalize()
                label = f"{t} ({product_name})" if t else product_name
                if qty_bags:
                    return f"{label} - {qty_bags} bag(s)"
                return f"{label} - {qty_kg:.2f}kg"

            rows: List[Dict[str, Any]] = []
            for sr in sale_returns:
                rows.append({
                    "kind": "sale_return",
                    "id": int(sr.sale_id),  # parent sale - see docstring above
                    "doc_no": f"SRET-{sr.id}",
                    "when": sr.created_at,
                    "party_name": parties.get(int(sr.party_id or 0), f"Party#{sr.party_id}"),
                    "amount": abs(_f(sr.amount)),
                    "created_by": users.get(int(sr.created_by_user_id or 0), ""),
                    "payment_status": sr.reason or "Returned",
                    "product_summary": _qty_summary(
                        products.get(int(sr.product_id or 0), f"Product#{sr.product_id}"),
                        sr.product_type_snapshot, _f(sr.qty_kg), int(sr.qty_bags or 0),
                    ),
                })
            for pr in purchase_returns:
                rows.append({
                    "kind": "purchase_return",
                    "id": int(pr.purchase_id),  # parent purchase - see docstring above
                    "doc_no": f"PRET-{pr.id}",
                    "when": pr.created_at,
                    "party_name": parties.get(int(pr.party_id or 0), f"Party#{pr.party_id}"),
                    "amount": abs(_f(pr.amount)),
                    "created_by": users.get(int(pr.created_by_user_id or 0), ""),
                    "payment_status": pr.reason or "Returned",
                    "product_summary": _qty_summary(
                        products.get(int(pr.product_id or 0), f"Product#{pr.product_id}"),
                        pr.product_type_snapshot, _f(pr.qty_kg), int(pr.qty_bags or 0),
                    ),
                })

            rows.sort(key=lambda r: r["when"] or datetime.min, reverse=True)
            return rows

    # ======================================================================
    # GLOBAL TRANSACTION SEARCH - "type SAL-123, find who created it and
    # who the customer/supplier was" - independent of any single cashier.
    # Unlike cashier_transactions (scoped to one user_id + one kind), this
    # searches doc_no across EVERY user and BOTH kinds (sale + purchase) at
    # once. Matching is a case-insensitive substring (ILIKE), so a partial
    # reference or just the numeric part still finds it.
    #
    # Deliberately NOT batch-optimized the way cashier_transactions is for
    # its full-history scan: this is bounded by `limit` (a doc_no search
    # only ever returns a handful of hits), so the same batched-query
    # shape is used for correctness/consistency, but there's no risk of
    # scanning the whole transactions table.
    # ======================================================================
    @staticmethod
    def search_transactions(query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Returns rows shaped like cashier_transactions(), PLUS
        created_by_user_id (so the caller can jump straight to that
        user's CashierBusinessHistoryDialog if desired). Empty/blank
        query returns an empty list rather than the whole table.

        Matches against the SAME value the UI actually displays as the
        reference - sale_no/purchase_no when populated, otherwise the
        computed fallback ("SAL-{id}"/"PUR-{id}") used everywhere else
        in this file (cashier_transactions, party_transactions, etc).
        Filtering the raw column alone silently hides every row whose
        sale_no/purchase_no is NULL - which is exactly what "SAL-9" not
        being found meant, even though it's the fallback label the
        dialog already shows for that sale.
        """
        q = (query or "").strip()
        if not q:
            return []

        with get_session() as s:
            like = f"%{q}%"

            sale_fallback = func.concat("SAL-", func.cast(Sale.id, String))
            sale_stmt = (
                select(Sale)
                .where(or_(
                    Sale.sale_no.ilike(like),
                    and_(
                        or_(Sale.sale_no.is_(None), Sale.sale_no == ""),
                        sale_fallback.ilike(like),
                    ),
                ))
                .order_by(Sale.created_at.desc())
                .limit(limit)
            )

            pur_fallback = func.concat("PUR-", func.cast(Purchase.id, String))
            pur_stmt = (
                select(Purchase)
                .where(or_(
                    Purchase.purchase_no.ilike(like),
                    and_(
                        or_(Purchase.purchase_no.is_(None), Purchase.purchase_no == ""),
                        pur_fallback.ilike(like),
                    ),
                ))
                .order_by(Purchase.created_at.desc())
                .limit(limit)
            )

            sales = s.execute(sale_stmt).scalars().all()
            purchases = s.execute(pur_stmt).scalars().all()

            if not sales and not purchases:
                return []

            parties = {int(p.id): (p.display_name or f"Party#{p.id}")
                       for p in s.query(Party).all()}
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = {int(p.id): (p.name or f"Product#{p.id}", p.product_type or "")
                        for p in s.query(Product).all()}

            sale_ids = [int(x.id) for x in sales]
            pur_ids = [int(x.id) for x in purchases]

            sale_lines: Dict[int, List[int]] = {}
            if sale_ids:
                for si in (s.query(SaleItem)
                          .filter(SaleItem.sale_id.in_(sale_ids)).all()):
                    sale_lines.setdefault(int(si.sale_id), []).append(int(si.product_id))
            pur_lines: Dict[int, List[int]] = {}
            if pur_ids:
                for pi in (s.query(PurchaseItem)
                          .filter(PurchaseItem.purchase_id.in_(pur_ids)).all()):
                    pur_lines.setdefault(int(pi.purchase_id), []).append(int(pi.product_id))

            def _product_summary(product_ids: List[int]) -> str:
                if not product_ids:
                    return ""
                names = [products.get(pid, (f"Product#{pid}", ""))[0] for pid in product_ids]
                types = [products.get(pid, ("", ""))[1] for pid in product_ids]
                if len(names) == 1:
                    t = types[0].capitalize() if types[0] else ""
                    return f"{t} ({names[0]})" if t else names[0]
                extra = len(names) - 1
                return f"{names[0]} +{extra} more" if extra > 0 else names[0]

            def _paid_and_alloc(entity_type: str, ids: List[int]):
                if not ids:
                    return {}, {}
                paid_rows = (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0.0))
                    .filter(Payment.entity_type == entity_type, Payment.entity_id.in_(ids))
                    # Only the settling direction - a refund (opposite
                    # direction) must not count as extra money paid.
                    .filter(Payment.direction == ("in" if entity_type == "sale" else "out"))
                    .filter(Payment.method != "advance")
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                )
                alloc_rows = (
                    s.query(AdvanceAllocation.entity_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
                    .filter(AdvanceAllocation.entity_type == entity_type, AdvanceAllocation.entity_id.in_(ids))
                    .group_by(AdvanceAllocation.entity_id).all()
                )
                return ({int(eid): _f(amt) for eid, amt in paid_rows},
                       {int(eid): _f(amt) for eid, amt in alloc_rows})

            sale_paid, sale_alloc = _paid_and_alloc("sale", sale_ids)
            pur_paid, pur_alloc = _paid_and_alloc("purchase", pur_ids)
            pur_adj = _purchase_adjustments(s, pur_ids)

            sale_returned: Dict[int, float] = {}
            if sale_ids:
                for eid, amt in (s.query(SaleReturn.sale_id, func.coalesce(func.sum(SaleReturn.amount), 0.0))
                                 .filter(SaleReturn.sale_id.in_(sale_ids))
                                 .group_by(SaleReturn.sale_id).all()):
                    sale_returned[int(eid)] = abs(_f(amt))
            pur_returned: Dict[int, float] = {}
            if pur_ids:
                for eid, amt in (s.query(PurchaseReturn.purchase_id, func.coalesce(func.sum(PurchaseReturn.amount), 0.0))
                                 .filter(PurchaseReturn.purchase_id.in_(pur_ids))
                                 .group_by(PurchaseReturn.purchase_id).all()):
                    pur_returned[int(eid)] = abs(_f(amt))
            sale_discounts: Dict[int, float] = {}
            if sale_ids:
                for eid, amt in (s.query(SaleDiscount.sale_id, func.coalesce(func.sum(SaleDiscount.amount), 0.0))
                                 .filter(SaleDiscount.sale_id.in_(sale_ids))
                                 .group_by(SaleDiscount.sale_id).all()):
                    sale_discounts[int(eid)] = _f(amt)

            def _status(net_total: float, settled: float) -> str:
                if net_total <= 0.005:
                    return "paid"
                if settled >= net_total - 0.01:
                    return "paid"
                if settled > 0.005:
                    return "partial"
                return "unpaid"

            rows: List[Dict[str, Any]] = []
            for sale in sales:
                sid = int(sale.id)
                party_id = int(sale.buyer_party_id or 0)
                net_total = max(_f(sale.total_amount) - sale_returned.get(sid, 0.0) - sale_discounts.get(sid, 0.0), 0.0)
                settled = sale_paid.get(sid, 0.0) + sale_alloc.get(sid, 0.0)
                rows.append({
                    "kind": "sale",
                    "id": sid,
                    "doc_no": sale.sale_no or f"SAL-{sale.id}",
                    "when": sale.created_at,
                    "party_name": parties.get(party_id, sale.walkin_name or f"Party#{party_id}"),
                    "amount": _f(sale.total_amount),
                    "created_by": users.get(int(sale.created_by_user_id or 0), ""),
                    "created_by_user_id": int(sale.created_by_user_id or 0),
                    "payment_status": "voided" if sale.status == "voided" else _status(net_total, settled),
                    "product_summary": _product_summary(sale_lines.get(sid, [])),
                })
            for pur in purchases:
                pid = int(pur.id)
                party_id = int(pur.supplier_party_id or 0)
                net_total = max(_f(pur.total_amount) - pur_returned.get(pid, 0.0) + pur_adj.get(pid, 0.0), 0.0)
                settled = pur_paid.get(pid, 0.0) + pur_alloc.get(pid, 0.0)
                rows.append({
                    "kind": "purchase",
                    "id": pid,
                    "doc_no": pur.purchase_no or f"PUR-{pur.id}",
                    "when": pur.created_at,
                    "party_name": parties.get(party_id, pur.walkin_name or f"Party#{party_id}"),
                    "amount": _f(pur.total_amount),
                    "created_by": users.get(int(pur.created_by_user_id or 0), ""),
                    "created_by_user_id": int(pur.created_by_user_id or 0),
                    "payment_status": "voided" if pur.status == "voided" else _status(net_total, settled),
                    "product_summary": _product_summary(pur_lines.get(pid, [])),
                })

            rows.sort(key=lambda r: r["when"] or datetime.min, reverse=True)
            return rows[:limit]