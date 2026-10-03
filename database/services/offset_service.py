# database/services/offset_service.py
"""
Offsets and swaps: settle what a party owes us with what we owe them, without cash.

    settle   a customer owes us for sales; he brings goods; we PURCHASE them and set the purchase
             against his unpaid sales. Whatever is left of the purchase we still owe him (pay it
             out as usual), whatever is left of the sales he still owes us.
    swap     he brings goods AND takes goods in one visit: a purchase and a sale are recorded
             together and set against each other; only the difference is left to pay either way.
    net off  a party we both owe and are owed by: cancel the overlap.

Offsets are rows in `document_offsets` (see the model); document_balance counts them as
settlements, so every screen agrees. They move no cash, so the drawer is untouched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func

from database.models.category import Category
from database.models.document_offset import DocumentOffset
from database.models.party import Party
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.models.stock_movement import STOCK_IN_TYPES, STOCK_OUT_TYPES, StockMovement
from database.services.audit_service import AuditService
from database.services.document_balance import document_balances
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_admin, require_role, system_context
from shared.auth.session import require_user_id
from shared.config.constants import WALKIN_PARTY_ID

EPS = 0.005


def _open_docs(s, side: str, party_id: int) -> List[Dict[str, Any]]:
    """Open (unsettled, not voided) documents of one party, oldest first.
    side: "creditors" = purchases we still owe on, "debtors" = sales they still owe on."""
    model, party_col, kind = ((Purchase, Purchase.supplier_party_id, "purchase") if side == "creditors"
                              else (Sale, Sale.buyer_party_id, "sale"))
    rows = (s.query(model.id, model.completed_at, model.created_at)
            .filter(model.status != "voided", party_col == int(party_id))
            .order_by(model.completed_at.asc(), model.id.asc()).all())
    balances = document_balances(s, kind, [r[0] for r in rows])
    out = []
    for did, completed, created in rows:
        b = balances.get(int(did))
        if b is not None and b.balance > EPS:
            out.append({"kind": kind, "doc_id": int(did), "date": completed or created, "balance": b.balance})
    return out


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class OffsetService:

    @staticmethod
    def preview(party_id: int, purchase_ids: Optional[Iterable[int]] = None,
                sale_ids: Optional[Iterable[int]] = None) -> Dict[str, float]:
        """What could be offset right now: what we owe them, what they owe us, and the overlap."""
        with get_session() as s:
            pay = OffsetService._pick(_open_docs(s, "creditors", int(party_id)), purchase_ids)
            rec = OffsetService._pick(_open_docs(s, "debtors", int(party_id)), sale_ids)
        we_owe = round(sum(d["balance"] for d in pay), 2)
        they_owe = round(sum(d["balance"] for d in rec), 2)
        return {"we_owe": we_owe, "they_owe": they_owe, "offsettable": round(min(we_owe, they_owe), 2)}

    @staticmethod
    def _pick(docs: List[Dict[str, Any]], ids: Optional[Iterable[int]]) -> List[Dict[str, Any]]:
        if ids is None:
            return docs
        wanted = {int(i) for i in ids}
        return [d for d in docs if d["doc_id"] in wanted]

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def apply(party_id: int, purchase_ids: Optional[Iterable[int]] = None,
              sale_ids: Optional[Iterable[int]] = None, amount: Optional[float] = None,
              kind: str = "settlement", note: str = "") -> Dict[str, Any]:
        """
        Set the party's open purchases against their open sales, oldest first on both sides.
        `purchase_ids` / `sale_ids` restrict a side to those documents (None = all open ones).
        `amount` caps the total (None = as much as both sides allow).
        """
        party_id = int(party_id)
        if party_id == WALKIN_PARTY_ID:
            raise ValueError("Walk-ins cannot be offset - they have no account of their own.")
        user_id = int(require_user_id())
        with get_session() as s:
            if s.query(Party.id).filter(Party.id == party_id).first() is None:
                raise ValueError("Party not found.")
            pay = OffsetService._pick(_open_docs(s, "creditors", party_id), purchase_ids)
            rec = OffsetService._pick(_open_docs(s, "debtors", party_id), sale_ids)
            we_owe = round(sum(d["balance"] for d in pay), 2)
            they_owe = round(sum(d["balance"] for d in rec), 2)
            if we_owe <= EPS:
                raise ValueError("Nothing is owed to this party on the chosen purchases.")
            if they_owe <= EPS:
                raise ValueError("This party has no unpaid sales to set it against.")
            total = min(we_owe, they_owe)
            if amount is not None:
                if _f(amount) <= 0:
                    raise ValueError("Enter an amount greater than zero.")
                if _f(amount) > total + EPS:
                    raise ValueError(f"At most {total:,.2f} can be offset.")
                total = round(_f(amount), 2)

            pairs: List[Dict[str, Any]] = []
            left = total
            pay_left = [[d, d["balance"]] for d in pay]
            rec_left = [[d, d["balance"]] for d in rec]
            pi = ri = 0
            while left > EPS and pi < len(pay_left) and ri < len(rec_left):
                p, r = pay_left[pi], rec_left[ri]
                part = round(min(left, p[1], r[1]), 2)
                if part > 0:
                    s.add(DocumentOffset(party_id=party_id, purchase_id=p[0]["doc_id"], sale_id=r[0]["doc_id"],
                                         amount=part, kind=kind, note=(note or "")[:255] or None,
                                         created_by_user_id=user_id, created_at=datetime.now()))
                    pairs.append({"purchase_id": p[0]["doc_id"], "sale_id": r[0]["doc_id"], "amount": part})
                p[1] = round(p[1] - part, 2)
                r[1] = round(r[1] - part, 2)
                left = round(left - part, 2)
                if p[1] <= EPS:
                    pi += 1
                if r[1] <= EPS:
                    ri += 1
            AuditService.record("offset.apply", "party", party_id,
                                f"Offset {total:,.2f} ({kind}): purchases set against sales", {"pairs": pairs}, session=s)
            s.commit()
        return {"amount": total, "pairs": pairs, "we_owe_left": round(we_owe - total, 2),
                "they_owe_left": round(they_owe - total, 2)}

    @staticmethod
    @require_admin
    def reverse(offset_id: int, reason: str) -> None:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("Give a reason for reversing this offset.")
        with get_session() as s:
            o = s.query(DocumentOffset).filter(DocumentOffset.id == int(offset_id)).with_for_update().first()
            if o is None:
                raise ValueError("Offset not found.")
            if o.status == "reversed":
                return
            o.status = "reversed"
            o.reversed_at = datetime.now()
            o.reversed_by_user_id = int(require_user_id())
            o.reverse_reason = reason[:255]
            AuditService.record("offset.reverse", "party", o.party_id,
                                f"Offset #{o.id} ({_f(o.amount):,.2f}) reversed: {reason}", session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def list_for_party(party_id: int, include_reversed: bool = True) -> List[Dict[str, Any]]:
        with get_session() as s:
            q = s.query(DocumentOffset).filter(DocumentOffset.party_id == int(party_id))
            if not include_reversed:
                q = q.filter(DocumentOffset.status == "active")
            return [{"id": int(o.id), "date": o.created_at, "purchase_id": int(o.purchase_id),
                     "sale_id": int(o.sale_id), "amount": _f(o.amount), "kind": o.kind, "note": o.note or "",
                     "status": o.status, "reverse_reason": o.reverse_reason or ""}
                    for o in q.order_by(DocumentOffset.id.desc()).all()]


class SwapService:
    """Goods for goods: one visit, one purchase + one sale, set against each other."""

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def products_to_give() -> List[Dict[str, Any]]:
        """Products we could hand over, with what is on the shelf right now."""
        with get_session() as s:
            prods = s.query(Product).filter(Product.is_active == True).order_by(Product.name).all()  # noqa: E712
            cats = {int(c.id): c.name for c in s.query(Category).all()}
            bags: Dict[int, float] = {}
            kg: Dict[int, float] = {}
            for pid, mtype, b, w in s.query(StockMovement.product_id, StockMovement.movement_type,
                                            func.coalesce(func.sum(StockMovement.qty_bags), 0),
                                            func.coalesce(func.sum(StockMovement.total_weight_kg), 0)
                                            ).group_by(StockMovement.product_id, StockMovement.movement_type):
                sign = 1 if mtype in STOCK_IN_TYPES else -1 if mtype in STOCK_OUT_TYPES else 0
                bags[int(pid)] = bags.get(int(pid), 0.0) + sign * _f(b)
                kg[int(pid)] = kg.get(int(pid), 0.0) + sign * _f(w)
            return [{"id": int(p.id), "name": p.name, "sku": p.sku, "type": str(p.product_type or "").lower(),
                     "price": _f(p.default_sale_rate), "category": cats.get(int(p.category_id or 0), ""),
                     "bags": bags.get(int(p.id), 0.0),
                     "kg": kg.get(int(p.id), 0.0)}
                    for p in prods]

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def record(party_id: int, received: List[Dict[str, Any]], given: List[Dict[str, Any]],
               note: str = "") -> Dict[str, Any]:
        """
        received: purchase lines (PurchaseService.record item shape) - what he brings us.
        given:    sale lines (SaleService.record item shape)         - what we hand him.
        """
        if not received:
            raise ValueError("Add the goods he is bringing us.")
        if not given:
            raise ValueError("Add the goods we are giving him.")
        return SwapService.record_from_records(party_id, [], [], received, given, note)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def record_from_records(party_id: int, purchase_ids: Iterable[int], sale_ids: Iterable[int],
                            received: List[Dict[str, Any]], given: List[Dict[str, Any]],
                            note: str = "") -> Dict[str, Any]:
        """
        A swap built from records already on the profile plus new goods.

          purchase_ids  purchases from him already recorded (what we still owe him on them)
          sale_ids      sales to him already recorded       (what he still owes us on them)
          received      NEW goods he brings now  -> recorded as one new purchase
          given         NEW goods we hand him now -> recorded as one new sale

        Everything on the "we owe him" side is set against everything on the "he owes us" side, oldest
        first, for the smaller total; only the difference is left to settle either way. Nothing is
        written unless both sides have something to set against. If a later step fails, the documents
        this call created are voided so a half-recorded swap never remains.
        """
        from database.services.purchase_service import PurchaseService
        from database.services.sales_service import SaleService

        party_id = int(party_id)
        purchase_ids, sale_ids = [int(i) for i in purchase_ids], [int(i) for i in sale_ids]
        received, given = list(received or []), list(given or [])
        if party_id <= 0 or party_id == WALKIN_PARTY_ID:
            raise ValueError("Choose a registered customer or supplier for a swap.")
        if not (purchase_ids or received):
            raise ValueError("Choose the purchases from him, or add the goods he is bringing us.")
        if not (sale_ids or given):
            raise ValueError("Choose the sales to him, or add the goods we are giving him.")
        user_id = int(require_user_id())
        label = ("Swap" + (f": {note.strip()}" if (note or "").strip() else ""))[:200]

        with get_session() as s:                      # every chosen record must be an open one of his
            open_p = {d["doc_id"]: d["balance"] for d in _open_docs(s, "creditors", party_id)}
            open_s = {d["doc_id"]: d["balance"] for d in _open_docs(s, "debtors", party_id)}
        for ids, open_, word in ((purchase_ids, open_p, "purchase"), (sale_ids, open_s, "sale")):
            bad = [i for i in ids if i not in open_]
            if bad:
                raise ValueError(f"{word.title()} {bad[0]} is not an open (unpaid) record of this customer.")
        new_in = round(sum(_buy_total(i) for i in received), 2)
        new_out = round(sum(_sell_total(i) for i in given), 2)
        we_owe = round(sum(open_p[i] for i in purchase_ids) + new_in, 2)
        they_owe = round(sum(open_s[i] for i in sale_ids) + new_out, 2)
        if we_owe <= EPS or they_owe <= EPS:
            raise ValueError("Nothing to set against: both sides need a value above zero.")

        purchase_payload = {"supplier_party_id": party_id, "created_by_user_id": user_id, "items": received,
                            "note": label, "auto_flag_supplier": True}
        if received:
            PurchaseService._validate_payload(purchase_payload)         # fail before anything is written

        sale = purchase = None
        try:
            if given:
                sale = SaleService.record({"buyer_party_id": party_id, "created_by_user_id": user_id,
                                           "items": given, "note": label})      # stock is checked here
            if received:
                purchase = PurchaseService.record(purchase_payload)
            res = OffsetService.apply(
                party_id, purchase_ids=purchase_ids + ([purchase["purchase_id"]] if purchase else []),
                sale_ids=sale_ids + ([sale["sale_id"]] if sale else []), kind="swap", note=label)
        except Exception:
            with system_context():
                if sale:
                    SaleService.void_sale(int(sale["sale_id"]), user_id, "Swap failed - not recorded")
                if purchase:
                    PurchaseService.void_purchase(int(purchase["purchase_id"]), user_id, "Swap failed - not recorded")
            raise

        with get_session() as s:
            AuditService.record("swap.record", "party", party_id,
                                f"Swap: he brings {we_owe:,.2f}, we give {they_owe:,.2f}",
                                {"purchase_ids": purchase_ids, "sale_ids": sale_ids,
                                 "new_purchase_id": purchase["purchase_id"] if purchase else None,
                                 "new_sale_id": sale["sale_id"] if sale else None}, session=s)
            s.commit()
        diff = round(we_owe - they_owe, 2)
        return {"purchase_id": int(purchase["purchase_id"]) if purchase else None,
                "sale_id": int(sale["sale_id"]) if sale else None,
                "purchase_total": we_owe, "sale_total": they_owe, "offset": res["amount"],
                "we_owe_him": max(diff, 0.0), "he_owes_us": max(-diff, 0.0)}


def _buy_total(it: Dict[str, Any]) -> float:
    crops = str(it.get("product_type", "")).lower() != "fertilizer"
    return _f(it["total_weight_kg"] if crops else it["qty_bags"]) * _f(it["unit_cost"])


def _sell_total(it: Dict[str, Any]) -> float:
    """A sale line: product_id, qty_bags, total_weight_kg (crops only), unit_price. Crops are sold by the kg."""
    kg = _f(it.get("total_weight_kg"))
    return (kg if kg > 0 else _f(it["qty_bags"])) * _f(it["unit_price"])
