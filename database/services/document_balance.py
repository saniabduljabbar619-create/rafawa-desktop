# database/services/document_balance.py
"""
ONE definition of "what does this sale/purchase still owe?".

Before this module the same arithmetic was hand-copied into the drawer (three
places), the admin dashboard, the supplier profile and the ledger, and the
copies had drifted (some ignored refunds' direction, price adjustments,
advance allocations or NULL payment methods). Everything that shows a
document's total / paid / balance / status now goes through here.

    net_total  = gross - returns - discounts (+ price adjustments for purchases), never < 0
    cash_paid  = real money in (sale) / out (purchase), excluding advance-funded
                 settlements and voided payments
    settled    = cash_paid + advance allocations
    balance    = max(net_total - settled, 0)

A voided document owes nothing (net_total 0) and has status "voided".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from sqlalchemy import func

from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment
from database.models.purchase import Purchase
from database.models.purchase_price_adjustment import PurchasePriceAdjustment
from database.models.purchase_return import PurchaseReturn
from database.models.sale import Sale
from database.models.sale_discount import SaleDiscount
from database.models.sale_return import SaleReturn

_EPS_OWED = 0.005      # below this much owed, a document counts as fully covered
_EPS_PAID = 0.01       # tolerance when comparing settled against the net total
_CHUNK = 500


@dataclass(frozen=True)
class DocBalance:
    kind: str
    doc_id: int
    voided: bool
    gross: float
    returned: float
    discount: float
    adjustment: float
    net_total: float
    cash_paid: float
    advance_applied: float
    settled: float
    balance: float
    status: str                      # "paid" | "partial" | "unpaid" | "voided"


def payment_status(net_total: float, settled: float) -> str:
    if net_total <= _EPS_OWED:
        return "paid"
    if settled >= net_total - _EPS_PAID:
        return "paid"
    if settled > _EPS_OWED:
        return "partial"
    return "unpaid"


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _chunks(ids):
    ids = sorted({int(i) for i in ids})
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def _sum_by(s, id_col, amount_col, ids, *extra_filters, absolute=False) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for chunk in _chunks(ids):
        q = (s.query(id_col, func.coalesce(func.sum(amount_col), 0))
             .filter(id_col.in_(chunk)))
        for f in extra_filters:
            q = q.filter(f)
        for eid, amt in q.group_by(id_col).all():
            out[int(eid)] = abs(_f(amt)) if absolute else _f(amt)
    return out


def document_balances(s, kind: str, ids: Iterable[int],
                      payments_by_user_id: Optional[int] = None) -> Dict[int, DocBalance]:
    """
    Balances for many documents of one kind ("sale" or "purchase"), using a
    handful of grouped queries regardless of how many ids are passed.

    payments_by_user_id: count only payments recorded by that user (used by the
    cashier-isolated dashboard views).
    """
    if kind not in ("sale", "purchase"):
        raise ValueError("kind must be 'sale' or 'purchase'")
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}

    doc_model = Sale if kind == "sale" else Purchase
    docs: Dict[int, tuple] = {}
    for chunk in _chunks(ids):
        for did, total, status in s.query(doc_model.id, doc_model.total_amount, doc_model.status) \
                .filter(doc_model.id.in_(chunk)).all():
            docs[int(did)] = (_f(total), str(status or "") == "voided")

    direction = "in" if kind == "sale" else "out"
    pay_filters = [
        Payment.entity_type == kind,
        Payment.direction == direction,
        Payment.status != "voided",
        func.coalesce(Payment.method, "") != "advance",
        func.coalesce(Payment.notes, "").notlike("%Advance settlement%"),
    ]
    if payments_by_user_id:
        pay_filters.append(Payment.created_by_user_id == int(payments_by_user_id))
    cash = _sum_by(s, Payment.entity_id, Payment.amount, ids, *pay_filters)
    alloc = _sum_by(s, AdvanceAllocation.entity_id, AdvanceAllocation.amount, ids,
                    AdvanceAllocation.entity_type == kind)

    if kind == "sale":
        returned = _sum_by(s, SaleReturn.sale_id, SaleReturn.amount, ids, absolute=True)
        discounts = _sum_by(s, SaleDiscount.sale_id, SaleDiscount.amount, ids)
        adjustments: Dict[int, float] = {}
    else:
        returned = _sum_by(s, PurchaseReturn.purchase_id, PurchaseReturn.amount, ids, absolute=True)
        adjustments = _sum_by(s, PurchasePriceAdjustment.purchase_id, PurchasePriceAdjustment.amount, ids)
        discounts = {}

    out: Dict[int, DocBalance] = {}
    for did in ids:
        if did not in docs:
            continue
        gross, voided = docs[did]
        ret, disc, adj = returned.get(did, 0.0), discounts.get(did, 0.0), adjustments.get(did, 0.0)
        net = 0.0 if voided else max(gross - ret - disc + adj, 0.0)
        paid, applied = cash.get(did, 0.0), alloc.get(did, 0.0)
        settled = paid + applied
        out[did] = DocBalance(
            kind=kind, doc_id=did, voided=voided, gross=gross, returned=ret, discount=disc,
            adjustment=adj, net_total=net, cash_paid=paid, advance_applied=applied,
            settled=settled, balance=max(net - settled, 0.0),
            status="voided" if voided else payment_status(net, settled))
    return out
