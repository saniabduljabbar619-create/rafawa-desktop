# database/services/drawer/compute.py
"""DrawerService.compute: the per-cashier drawer figures (cards, cash events, cross-cashier lines, verdicts)."""
from __future__ import annotations

from .ledger import LedgerMixin
from ._common import (
    _who,
    Advance,
    Any,
    CashierSettlement,
    Dict,
    List,
    Optional,
    Party,
    Payment,
    Purchase,
    PurchaseReturn,
    Sale,
    SaleReturn,
    User,
    _FieldView,
    _LazyMap,
    _f,
    _outside_range_filter,
    _range_filter,
    and_,
    date,
    datetime,
    func,
    get_session,
    or_,
    select,
    time,
)


class ComputeMixin:

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
            parties = _LazyMap(lambda ids: {
                int(p.id): (f"{p.display_name} ({p.walkin_code})" if p.walkin_code else (p.display_name or f"Party#{p.id}"))
                for p in s.query(Party.id, Party.display_name, Party.walkin_code)
                .filter(Party.id.in_(ids)).all()})

            # -------- creator maps for documents (id -> creator, party, no),
            # loaded lazily for just the ids this call actually touches.
            sale_meta = _LazyMap(lambda ids: {
                int(r.id): (int(r.created_by_user_id or 0),
                            int(r.buyer_party_id or 0),
                            r.sale_no or f"SAL-{r.id}",
                            r.walkin_name or "",
                            r.created_at)
                for r in s.query(Sale.id, Sale.created_by_user_id, Sale.buyer_party_id,
                                 Sale.sale_no, Sale.walkin_name, Sale.created_at)
                .filter(Sale.id.in_(ids)).all()})
            pur_meta = _LazyMap(lambda ids: {
                int(r.id): (int(r.created_by_user_id or 0),
                            int(r.supplier_party_id or 0),
                            r.purchase_no or f"PUR-{r.id}",
                            r.walkin_name or "",
                            r.created_at)
                for r in s.query(Purchase.id, Purchase.created_by_user_id, Purchase.supplier_party_id,
                                 Purchase.purchase_no, Purchase.walkin_name, Purchase.created_at)
                .filter(Purchase.id.in_(ids)).all()})
            # 6th element = the advance's OWN direction (received/given) - NOT
            # the settling Payment's direction, which follows an inverted
            # bookkeeping convention for advances (settle_advance_payment
            # deliberately writes direction="out" for settling a RECEIVED
            # advance). Using payment.direction to classify an advance bucket
            # silently swaps advance_in and advance_out the moment a real
            # settlement happens - this is what needs to drive the bucket
            # everywhere below. The 5-tuple slice shape used by the helpers
            # below is unchanged.
            adv_meta = _LazyMap(lambda ids: {
                int(r.id): (int(r.created_by_user_id or 0),
                            int(r.party_id or 0),
                            f"ADV-{r.id}", "",
                            r.created_at,
                            str(r.direction or ""))
                for r in s.query(Advance.id, Advance.created_by_user_id, Advance.party_id,
                                 Advance.created_at, Advance.direction)
                .filter(Advance.id.in_(ids)).all()})
            adv_direction = _FieldView(adv_meta, 5)

            def _prefetch_payment_docs(payments) -> None:
                by_type = {"sale": set(), "purchase": set(), "advance": set()}
                for p in payments:
                    et = str(p.entity_type or "")
                    if et in by_type:
                        by_type[et].add(int(p.entity_id or 0))
                sale_meta.prefetch(by_type["sale"])
                pur_meta.prefetch(by_type["purchase"])
                adv_meta.prefetch(by_type["advance"])
                party_ids = set()
                for m in (sale_meta, pur_meta, adv_meta):
                    for row in m.values():
                        party_ids.add(row[1])
                parties.prefetch(party_ids)

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
            LedgerMixin._sync_ledger(s, _creator_party_no, _prefetch_payment_docs)

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

            _prefetch_payment_docs(my_payments)

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

            _prefetch_payment_docs([row[0] for _et, row in old_settlement_rows])

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
                    "party": _who(parties, party_id, walkin),
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
                    "party": _who(parties, party_id, walkin),
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

            parties.prefetch([int(a.party_id or 0) for a in my_advances])
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
            settled_by_adv: Dict[int, float] = {}
            if my_advs:
                settled_by_adv = {
                    int(eid): _f(total)
                    for eid, total in s.query(Payment.entity_id,
                                              func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "advance",
                            Payment.entity_id.in_([int(a.id) for a in my_advs]),
                            Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                }
            for a in my_advs:
                amt = _f(a.amount)
                settled = settled_by_adv.get(int(a.id), 0.0)
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
            # A line only matters here when this user is its debtor or its
            # creditor, i.e. when they either SETTLED the payment or CREATED
            # the document it settles - so only those payments are loaded,
            # instead of every payment in the system.
            _uid = int(user_id)
            _i_created_the_doc = or_(
                and_(Payment.entity_type == "sale",
                     select(Sale.id).where(Sale.id == Payment.entity_id,
                                           Sale.created_by_user_id == _uid).exists()),
                and_(Payment.entity_type == "purchase",
                     select(Purchase.id).where(Purchase.id == Payment.entity_id,
                                               Purchase.created_by_user_id == _uid).exists()),
                and_(Payment.entity_type == "advance",
                     select(Advance.id).where(Advance.id == Payment.entity_id,
                                              Advance.created_by_user_id == _uid).exists()),
            )
            all_pay_stmt = select(Payment).where(
                func.lower(func.coalesce(Payment.method, "")) != "advance"
            ).where(Payment.status != "voided").where(
                # settler == creator is never a cross-cashier line, so skip
                # payments where I did both.
                or_(
                    and_(Payment.created_by_user_id == _uid, ~_i_created_the_doc),
                    and_(Payment.created_by_user_id != _uid, _i_created_the_doc),
                )
            )
            # Deliberately NOT date-filtered, despite start_date/end_date being
            # available here - an open cross-cashier debt does not expire or
            # become invisible just because the activity cards above are
            # scoped to today. Reconciliation must reflect the TRUE, current
            # open/settled state regardless of what date range the rest of
            # the screen happens to be showing.
            # Only the scalar columns this section reads - not whole ORM rows.
            all_payments = s.execute(all_pay_stmt.with_only_columns(
                Payment.id, Payment.entity_type, Payment.entity_id,
                Payment.direction, Payment.amount, Payment.notes,
                Payment.created_at, Payment.created_by_user_id)).all()
            _prefetch_payment_docs(all_payments)

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
                    "party": _who(parties, party_id, walkin),
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
