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
from database.services.document_balance import document_balances
from database.services.audit_service import AuditService
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


class _LazyMap(dict):
    """
    A dict that fills itself from the database on demand, in batches.

    compute() used to load EVERY sale, purchase, advance and party into
    memory on each call just to look up a handful of ids. This loads only the
    ids actually touched: prefetch() fetches a whole batch in a few IN()
    queries, and .get() falls back to a single lookup for anything that was
    not prefetched (misses are remembered so they are never re-queried).
    """
    _CHUNK = 500

    def __init__(self, loader):
        super().__init__()
        self._loader = loader
        self._missing = set()

    def prefetch(self, ids) -> None:
        need = sorted({int(i) for i in ids if i is not None} - self.keys() - self._missing)
        for i in range(0, len(need), self._CHUNK):
            chunk = need[i:i + self._CHUNK]
            got = self._loader(chunk)
            self.update(got)
            self._missing |= set(chunk) - set(got)

    def get(self, key, default=None):
        try:
            k = int(key)
        except (TypeError, ValueError):
            return default
        if k not in self and k not in self._missing:
            self.prefetch([k])
        return super().get(k, default)


def _party_map(s) -> "_LazyMap":
    """id -> name; a walk-in's ID code is added so two people with one name are told apart."""
    return _LazyMap(lambda ids: {
        int(i): (f"{n} ({code})" if code else (n or f"Party#{i}"))
        for i, n, code in s.query(Party.id, Party.display_name, Party.walkin_code).filter(Party.id.in_(ids)).all()})


def _who(parties, party_id, walkin, fallback="") -> str:
    """Name for a document's party. Every casual walk-in sits on the one shared account, so use the typed name."""
    from shared.config.constants import WALKIN_PARTY_ID
    w = (walkin or "").strip()
    if int(party_id or 0) == WALKIN_PARTY_ID and w:
        return f"{w} (Walk-in)"
    return parties.get(party_id, w or fallback)


def _product_map(s) -> "_LazyMap":
    """id -> (name, product_type)."""
    return _LazyMap(lambda ids: {
        int(i): (n or f"Product#{i}", t or "")
        for i, n, t in s.query(Product.id, Product.name, Product.product_type)
        .filter(Product.id.in_(ids)).all()})


def _product_name_map(s) -> "_LazyMap":
    """id -> name."""
    return _LazyMap(lambda ids: {
        int(i): (n or f"Product#{i}")
        for i, n in s.query(Product.id, Product.name).filter(Product.id.in_(ids)).all()})


class _FieldView:
    """Read-only view onto one element of the tuples in a _LazyMap."""

    def __init__(self, source: "_LazyMap", index: int):
        self._source, self._index = source, index

    def get(self, key, default=None):
        row = self._source.get(key)
        return row[self._index] if row is not None else default
