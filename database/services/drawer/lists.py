# database/services/drawer/lists.py
"""Drawer drill-down lists: party totals, party/cashier transactions, returns, global search."""
from __future__ import annotations

from ._common import (
    Any,
    Dict,
    List,
    Optional,
    Party,
    Payment,
    Product,
    Purchase,
    PurchaseItem,
    PurchaseReturn,
    Sale,
    SaleItem,
    SaleReturn,
    String,
    User,
    _f,
    _party_map,
    _product_map,
    _product_name_map,
    _range_filter,
    and_,
    date,
    datetime,
    document_balances,
    func,
    get_session,
    or_,
    select,
    time,
)


class ListsMixin:

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

            # Only the columns the totals need - not whole ORM rows.
            sales = s.execute(sale_stmt.with_only_columns(
                Sale.buyer_party_id, Sale.total_amount, Sale.walkin_name)).all()
            purchases = s.execute(pur_stmt.with_only_columns(
                Purchase.supplier_party_id, Purchase.total_amount, Purchase.walkin_name)).all()

            parties = _party_map(s)
            parties.prefetch([int(r.buyer_party_id or 0) for r in sales]
                             + [int(r.supplier_party_id or 0) for r in purchases])
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
            products = _product_map(s)

            # ---- product summaries, one batch query per document type ----
            sale_lines: Dict[int, List[int]] = {}
            if sale_ids:
                for sid_, pid_ in (s.query(SaleItem.sale_id, SaleItem.product_id)
                                   .filter(SaleItem.sale_id.in_(sale_ids)).all()):
                    sale_lines.setdefault(int(sid_), []).append(int(pid_))

            pur_lines: Dict[int, List[int]] = {}
            if pur_ids:
                for pid_, prod_ in (s.query(PurchaseItem.purchase_id, PurchaseItem.product_id)
                                    .filter(PurchaseItem.purchase_id.in_(pur_ids)).all()):
                    pur_lines.setdefault(int(pid_), []).append(int(prod_))

            products.prefetch([pid for ids_ in list(sale_lines.values()) + list(pur_lines.values())
                               for pid in ids_])

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

            # Paid / balance / status come from the single shared definition.
            sale_bal = document_balances(s, "sale", sale_ids)
            pur_bal = document_balances(s, "purchase", pur_ids)

            rows: List[Dict[str, Any]] = []
            for sale in sales:
                sid = int(sale.id)
                rows.append({
                    "kind": "sale",
                    "id": sid,
                    "doc_no": sale.sale_no or f"SAL-{sale.id}",
                    "when": sale.created_at,
                    "amount": _f(sale.total_amount),
                    "created_by": users.get(int(sale.created_by_user_id or 0), ""),
                    "notes": sale.notes or "",
                    "payment_status": sale_bal[sid].status,
                    "product_summary": _product_summary(sale_lines.get(sid, [])),
                })
            for pur in purchases:
                pid = int(pur.id)
                rows.append({
                    "kind": "purchase",
                    "id": pid,
                    "doc_no": pur.purchase_no or f"PUR-{pur.id}",
                    "when": pur.created_at,
                    "amount": _f(pur.total_amount),
                    "created_by": users.get(int(pur.created_by_user_id or 0), ""),
                    "notes": pur.notes or "",
                    "payment_status": pur_bal[pid].status,
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
            return ListsMixin._cashier_returns(user_id, start_date, end_date)

        with get_session() as s:
            if kind == "sale":
                stmt = select(Sale.id, Sale.buyer_party_id, Sale.sale_no, Sale.walkin_name,
                              Sale.total_amount, Sale.created_by_user_id, Sale.created_at,
                              Sale.status).where(Sale.created_by_user_id == user_id)
                stmt = _range_filter(stmt, Sale.created_at, start_date, end_date)
                docs = s.execute(stmt).all()
            else:
                stmt = select(Purchase.id, Purchase.supplier_party_id, Purchase.purchase_no,
                              Purchase.walkin_name, Purchase.total_amount,
                              Purchase.created_by_user_id, Purchase.created_at,
                              Purchase.status).where(Purchase.created_by_user_id == user_id)
                stmt = _range_filter(stmt, Purchase.created_at, start_date, end_date)
                docs = s.execute(stmt).all()

            doc_ids = [int(d.id) for d in docs]

            parties = _party_map(s)
            parties.prefetch([int((d.buyer_party_id if kind == "sale" else d.supplier_party_id) or 0)
                              for d in docs])
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = _product_map(s)

            # ---- product summaries, one batch query for the whole set ----
            item_lines: Dict[int, List[int]] = {}
            if doc_ids:
                if kind == "sale":
                    for sid_, pid_ in (s.query(SaleItem.sale_id, SaleItem.product_id)
                                       .filter(SaleItem.sale_id.in_(doc_ids)).all()):
                        item_lines.setdefault(int(sid_), []).append(int(pid_))
                else:
                    for pid_, prod_ in (s.query(PurchaseItem.purchase_id, PurchaseItem.product_id)
                                        .filter(PurchaseItem.purchase_id.in_(doc_ids)).all()):
                        item_lines.setdefault(int(pid_), []).append(int(prod_))
            products.prefetch([pid for ids_ in item_lines.values() for pid in ids_])

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

            # Paid / balance / status come from the single shared definition.
            balances = document_balances(s, kind, doc_ids)

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

                rows.append({
                    "kind": kind,
                    "id": did,
                    "doc_no": doc_no,
                    "when": d.created_at,
                    "party_name": party_name,
                    "amount": raw_total,
                    "created_by": users.get(int(d.created_by_user_id or 0), ""),
                    "payment_status": balances[did].status,
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

            parties = _party_map(s)
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = _product_name_map(s)
            parties.prefetch([int(r.party_id or 0) for r in sale_returns]
                             + [int(r.party_id or 0) for r in purchase_returns])
            products.prefetch([int(r.product_id or 0) for r in sale_returns]
                              + [int(r.product_id or 0) for r in purchase_returns])

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

            parties = _party_map(s)
            users = {int(u.id): (u.username or f"User#{u.id}")
                     for u in s.query(User).all()}
            products = _product_map(s)

            sale_ids = [int(x.id) for x in sales]
            pur_ids = [int(x.id) for x in purchases]

            sale_lines: Dict[int, List[int]] = {}
            if sale_ids:
                for sid_, pid_ in (s.query(SaleItem.sale_id, SaleItem.product_id)
                                   .filter(SaleItem.sale_id.in_(sale_ids)).all()):
                    sale_lines.setdefault(int(sid_), []).append(int(pid_))
            pur_lines: Dict[int, List[int]] = {}
            if pur_ids:
                for pid_, prod_ in (s.query(PurchaseItem.purchase_id, PurchaseItem.product_id)
                                    .filter(PurchaseItem.purchase_id.in_(pur_ids)).all()):
                    pur_lines.setdefault(int(pid_), []).append(int(prod_))

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

            # Paid / balance / status come from the single shared definition.
            sale_bal = document_balances(s, "sale", sale_ids)
            pur_bal = document_balances(s, "purchase", pur_ids)

            parties.prefetch([int(x.buyer_party_id or 0) for x in sales]
                             + [int(x.supplier_party_id or 0) for x in purchases])
            products.prefetch([pid for ids_ in list(sale_lines.values()) + list(pur_lines.values())
                               for pid in ids_])

            rows: List[Dict[str, Any]] = []
            for sale in sales:
                sid = int(sale.id)
                party_id = int(sale.buyer_party_id or 0)
                rows.append({
                    "kind": "sale",
                    "id": sid,
                    "doc_no": sale.sale_no or f"SAL-{sale.id}",
                    "when": sale.created_at,
                    "party_name": parties.get(party_id, sale.walkin_name or f"Party#{party_id}"),
                    "amount": _f(sale.total_amount),
                    "created_by": users.get(int(sale.created_by_user_id or 0), ""),
                    "created_by_user_id": int(sale.created_by_user_id or 0),
                    "payment_status": sale_bal[sid].status,
                    "product_summary": _product_summary(sale_lines.get(sid, [])),
                })
            for pur in purchases:
                pid = int(pur.id)
                party_id = int(pur.supplier_party_id or 0)
                rows.append({
                    "kind": "purchase",
                    "id": pid,
                    "doc_no": pur.purchase_no or f"PUR-{pur.id}",
                    "when": pur.created_at,
                    "party_name": parties.get(party_id, pur.walkin_name or f"Party#{party_id}"),
                    "amount": _f(pur.total_amount),
                    "created_by": users.get(int(pur.created_by_user_id or 0), ""),
                    "created_by_user_id": int(pur.created_by_user_id or 0),
                    "payment_status": pur_bal[pid].status,
                    "product_summary": _product_summary(pur_lines.get(pid, [])),
                })

            rows.sort(key=lambda r: r["when"] or datetime.min, reverse=True)
            return rows[:limit]
