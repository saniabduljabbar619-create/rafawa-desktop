# database/services/report_service.py
"""
Management reports. Pure data (lists of dicts) so the same numbers feed the
screen, the Excel export and the tests.

  period_summary(start, end)      sales / purchases / returns / cash movement for a date range
  aging(kind, as_of)              who owes us (sale) / whom we owe (purchase), by age of the document
  stock_valuation()               what the stock on hand is worth at cost

All "owed" figures come from document_balance - the one shared definition.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func

from database.models.advance import Advance
from database.models.party import Party
from database.models.payment import Payment
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.purchase_return import PurchaseReturn
from database.models.sale import Sale
from database.models.sale_discount import SaleDiscount
from database.models.sale_return import SaleReturn
from database.models.stock_movement import STOCK_IN_TYPES, STOCK_OUT_TYPES, StockMovement
from database.models.user import User
from database.services.document_balance import document_balances
from database.session import get_session
from shared.auth.permissions import require_admin

AGE_BUCKETS = (("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10 ** 6))


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _bounds(start: Optional[date], end: Optional[date]):
    lo = datetime.combine(start, time.min) if start else None
    hi = datetime.combine(end, time.max) if end else None
    return lo, hi


def _in_range(col, lo, hi):
    conds = []
    if lo is not None:
        conds.append(col >= lo)
    if hi is not None:
        conds.append(col <= hi)
    return conds


class ReportService:

    # ------------------------------------------------------------------ period summary
    @staticmethod
    @require_admin
    def period_summary(start: Optional[date] = None, end: Optional[date] = None) -> Dict[str, Any]:
        lo, hi = _bounds(start, end)
        with get_session() as s:
            def docs(model, party_col):
                q = s.query(model.id, model.total_amount, model.created_by_user_id, party_col) \
                    .filter(model.status != "voided")
                for c in _in_range(model.created_at, lo, hi):
                    q = q.filter(c)
                return q.all()

            sales = docs(Sale, Sale.buyer_party_id)
            purchases = docs(Purchase, Purchase.supplier_party_id)
            sale_ids = [r[0] for r in sales]

            def returns_total(model, ts_col):
                q = s.query(func.coalesce(func.sum(func.abs(model.amount)), 0))
                for c in _in_range(ts_col, lo, hi):
                    q = q.filter(c)
                return _f(q.scalar())

            sale_returns = returns_total(SaleReturn, SaleReturn.created_at)
            purchase_returns = returns_total(PurchaseReturn, PurchaseReturn.created_at)
            discounts = 0.0
            if sale_ids:
                discounts = _f(s.query(func.coalesce(func.sum(SaleDiscount.amount), 0))
                               .filter(SaleDiscount.sale_id.in_(sale_ids)).scalar())

            pay_q = (s.query(Payment.direction, func.coalesce(Payment.method, ""), Payment.created_by_user_id,
                             func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id))
                     .filter(Payment.status != "voided")
                     .filter(Payment.entity_type.in_(("sale", "purchase")))
                     .filter(func.coalesce(Payment.method, "") != "advance"))
            for c in _in_range(Payment.created_at, lo, hi):
                pay_q = pay_q.filter(c)
            pay_rows = pay_q.group_by(Payment.direction, func.coalesce(Payment.method, ""),
                                      Payment.created_by_user_id).all()

            adv_q = s.query(Advance.direction, func.coalesce(func.sum(Advance.amount), 0)) \
                .filter(Advance.status == "completed", Advance.advance_type == "money")
            for c in _in_range(Advance.created_at, lo, hi):
                adv_q = adv_q.filter(c)
            adv = {d: _f(t) for d, t in adv_q.group_by(Advance.direction).all()}

            users = {int(u.id): u.username for u in s.query(User.id, User.username).all()}

        money_in = money_out = 0.0
        by_method: Dict[str, Dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0})
        by_cashier: Dict[int, Dict[str, float]] = defaultdict(
            lambda: {"sales": 0.0, "purchases": 0.0, "cash_in": 0.0, "cash_out": 0.0})
        for direction, method, uid, total, _n in pay_rows:
            total = _f(total)
            by_method[method or "unspecified"][direction] += total
            by_cashier[int(uid or 0)]["cash_in" if direction == "in" else "cash_out"] += total
            if direction == "in":
                money_in += total
            else:
                money_out += total
        for _id, total, uid, _party in sales:
            by_cashier[int(uid or 0)]["sales"] += _f(total)
        for _id, total, uid, _party in purchases:
            by_cashier[int(uid or 0)]["purchases"] += _f(total)

        gross_sales = sum(_f(r[1]) for r in sales)
        gross_purchases = sum(_f(r[1]) for r in purchases)
        return {
            "start": start, "end": end,
            "sales": {"count": len(sales), "gross": gross_sales, "returns": sale_returns,
                      "manual_discounts": discounts, "net": gross_sales - sale_returns - discounts},
            "purchases": {"count": len(purchases), "gross": gross_purchases, "returns": purchase_returns,
                          "net": gross_purchases - purchase_returns},
            "cash": {"in": money_in, "out": money_out, "net": money_in - money_out},
            "advances": {"received": adv.get("received", 0.0), "given": adv.get("given", 0.0)},
            "by_method": [{"method": m, "in": v["in"], "out": v["out"], "net": v["in"] - v["out"]}
                          for m, v in sorted(by_method.items())],
            "by_cashier": [{"user_id": uid, "username": users.get(uid, f"User#{uid}"), **v}
                           for uid, v in sorted(by_cashier.items(), key=lambda kv: users.get(kv[0], ""))],
        }

    # ------------------------------------------------------------------ aging
    @staticmethod
    @require_admin
    def aging(kind: str, as_of: Optional[date] = None) -> List[Dict[str, Any]]:
        """
        One row per party with money still outstanding on non-voided documents of
        `kind` ("sale" = receivable from customers, "purchase" = payable to suppliers),
        split into age buckets by document date.
        """
        if kind not in ("sale", "purchase"):
            raise ValueError("kind must be 'sale' or 'purchase'")
        as_of = as_of or date.today()
        model = Sale if kind == "sale" else Purchase
        party_col = Sale.buyer_party_id if kind == "sale" else Purchase.supplier_party_id
        with get_session() as s:
            docs = s.query(model.id, party_col, model.created_at, model.walkin_name) \
                .filter(model.status != "voided").all()
            balances = document_balances(s, kind, [d[0] for d in docs])
            names = {int(i): n for i, n in s.query(Party.id, Party.display_name).all()}

        parties: Dict[int, Dict[str, Any]] = {}
        for did, pid, created, walkin in docs:
            bal = balances.get(int(did))
            if bal is None or bal.balance <= 0.005:
                continue
            age = max((as_of - (created.date() if created else as_of)).days, 0)
            row = parties.setdefault(int(pid), {
                "party_id": int(pid), "party": names.get(int(pid)) or walkin or f"Party#{pid}",
                "documents": 0, "total": 0.0, **{label: 0.0 for label, _, _ in AGE_BUCKETS}})
            for label, lo_d, hi_d in AGE_BUCKETS:
                if lo_d <= age <= hi_d:
                    row[label] += bal.balance
                    break
            row["documents"] += 1
            row["total"] += bal.balance
        return sorted(parties.values(), key=lambda r: -r["total"])

    # ------------------------------------------------------------------ stock valuation
    @staticmethod
    @require_admin
    def stock_valuation(include_zero: bool = False) -> List[Dict[str, Any]]:
        """On-hand stock per active product, valued at its default cost rate
        (per kg for crops, per bag for fertilizer)."""
        bags = func.coalesce(func.sum(case(
            (StockMovement.movement_type.in_(STOCK_IN_TYPES), StockMovement.qty_bags),
            (StockMovement.movement_type.in_(STOCK_OUT_TYPES), -StockMovement.qty_bags), else_=0)), 0)
        kg = func.coalesce(func.sum(case(
            (StockMovement.movement_type.in_(STOCK_IN_TYPES), StockMovement.total_weight_kg),
            (StockMovement.movement_type.in_(STOCK_OUT_TYPES), -StockMovement.total_weight_kg), else_=0)), 0)
        with get_session() as s:
            rows = (s.query(Product.id, Product.name, Product.sku, Product.product_type,
                            Product.default_cost_rate, bags, kg)
                    .outerjoin(StockMovement, StockMovement.product_id == Product.id)
                    .filter(Product.is_active.is_(True))
                    .group_by(Product.id, Product.name, Product.sku, Product.product_type,
                              Product.default_cost_rate)
                    .order_by(Product.name).all())
        out = []
        for pid, name, sku, ptype, cost, b, k in rows:
            b, k, cost = _f(b), _f(k), _f(cost)
            is_fert = str(ptype or "").lower() in ("fertilizer", "fertilizers")
            qty = b if is_fert else k
            if qty <= 0 and not include_zero:
                continue
            out.append({"product_id": int(pid), "product": name, "sku": sku or "", "type": ptype,
                        "bags": b, "kg": 0.0 if is_fert else k, "unit": "bag" if is_fert else "kg",
                        "unit_cost": cost, "value": qty * cost})
        return out
