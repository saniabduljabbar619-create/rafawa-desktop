# database/services/balance_statement_service.py
"""
The facts behind a printed balance statement ("what is left"), for one sale, purchase or advance - and for
everything still pending on the same customer / supplier.

`BalanceStatementService.for_document(kind, id)` -> plain dict (no ORM objects), so the receipt text can be
produced in any language without touching the database again:

    {
      "kind": "sale" | "purchase" | "advance",   "ref": "SAL-00012",
      "party_id", "name", "phone", "when", "served_by",
      "current": {...}                      # this document only (see _current_*)
      "pending": {                          # everything still open for the same party
          "sales":       [ {ref, date, total, paid, balance, status}, ... ],   # they owe us
          "purchases":   [ ... ],                                                 # we owe them
          "advance_out": [ {ref, date, amount, used, remaining, status}, ... ],  # money we gave, still unused
          "advance_in":  [ ... ],                                                 # money they gave, still unused
          "they_owe_us": sales balance + Advance OUT left,
          "we_owe_them": purchases balance + Advance IN left,
          "net":         they_owe_us - we_owe_them   (positive: they owe us),
      },
      "has_pending": bool,
    }

The shared walk-in account is a bucket for many unrelated people, so for it "everything pending" means only
the documents carrying the same walk-in name (and it has no advance account).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.party import Party
from database.models.payment import Payment
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.user import User
from database.services.document_balance import document_balances
from database.services.open_items import open_advances, open_documents
from database.services.walkin_account import is_shared_walkin
from database.session import get_session

_PREFIX = {"sale": "SAL", "purchase": "PUR", "advance": "ADV"}


def _party_name(party: Optional[Party], walkin_name: str = "") -> str:
    name = party.display_name if party else ""
    if party is not None and party.walkin_code:
        name = f"{name} ({party.walkin_code})"
    if walkin_name:
        name = walkin_name
    return name or ""


def _pending(s, party_id: int, shared_walkin: bool, walkin_name: str) -> Dict[str, Any]:
    scope = (walkin_name or "") if shared_walkin else None
    sales = open_documents(s, "sale", party_id=party_id, walkin_name=scope)
    purchases = open_documents(s, "purchase", party_id=party_id, walkin_name=scope)
    adv_out = [] if shared_walkin else open_advances(s, "given", party_id=party_id)
    adv_in = [] if shared_walkin else open_advances(s, "received", party_id=party_id)
    sales_bal = round(sum(d["balance"] for d in sales), 2)
    purch_bal = round(sum(d["balance"] for d in purchases), 2)
    out_left = round(sum(a["remaining"] for a in adv_out), 2)
    in_left = round(sum(a["remaining"] for a in adv_in), 2)
    they_owe = round(sales_bal + out_left, 2)
    we_owe = round(purch_bal + in_left, 2)
    return {
        "sales": sales, "purchases": purchases, "advance_out": adv_out, "advance_in": adv_in,
        "sales_balance": sales_bal, "purchases_balance": purch_bal,
        "advance_out_left": out_left, "advance_in_left": in_left,
        "they_owe_us": they_owe, "we_owe_them": we_owe, "net": round(they_owe - we_owe, 2),
    }


class BalanceStatementService:

    @staticmethod
    def resolve(kind: str, entity_id: int):
        """A payment is printed against the document it paid. Returns (kind, id) of a sale/purchase/advance."""
        kind = (kind or "").strip().lower()
        if kind != "payment":
            return kind, int(entity_id)
        with get_session() as s:
            pay = s.get(Payment, int(entity_id))
            if not pay:
                raise ValueError(f"Payment not found: {entity_id}")
            et = (pay.entity_type or "").lower()
            if et not in ("sale", "purchase", "advance"):
                raise ValueError("This payment is not against a sale, purchase or advance.")
            return et, int(pay.entity_id)

    @staticmethod
    def for_document(kind: str, entity_id: int) -> Dict[str, Any]:
        kind, entity_id = BalanceStatementService.resolve(kind, entity_id)
        if kind not in _PREFIX:
            raise ValueError(f"No balance statement for '{kind}'.")
        with get_session() as s:
            if kind == "advance":
                return BalanceStatementService._advance(s, entity_id)
            return BalanceStatementService._sale_or_purchase(s, kind, entity_id)

    @staticmethod
    def for_party(party_id: int, walkin_name: str = "") -> Dict[str, Any]:
        """Total balance only (no single document) - used when nothing particular is selected."""
        with get_session() as s:
            party = s.get(Party, int(party_id))
            shared = is_shared_walkin(int(party_id), s)
            wname = (walkin_name or "").strip() if shared else ""
            pend = _pending(s, int(party_id), shared, wname)
            return {
                "kind": "party", "ref": "", "party_id": int(party_id),
                "name": _party_name(party, wname), "phone": (party.phone or "") if party else "",
                "when": None, "served_by": "", "current": None, "pending": pend,
                "has_pending": bool(pend["they_owe_us"] or pend["we_owe_them"]),
            }

    # ------------------------------------------------------------------
    @staticmethod
    def _sale_or_purchase(s, kind: str, doc_id: int) -> Dict[str, Any]:
        model = Sale if kind == "sale" else Purchase
        doc = s.get(model, doc_id)
        if not doc:
            raise ValueError(f"{kind.title()} not found: {doc_id}")
        party_id = int(doc.buyer_party_id if kind == "sale" else doc.supplier_party_id)
        party = s.get(Party, party_id)
        shared = is_shared_walkin(party_id, s)
        wname = (doc.walkin_name or "").strip() if shared else ""
        if kind == "sale":
            rows = (s.query(SaleItem, Product).join(Product, Product.id == SaleItem.product_id)
                    .filter(SaleItem.sale_id == doc.id).all())
        else:
            rows = (s.query(PurchaseItem, Product).join(Product, Product.id == PurchaseItem.product_id)
                    .filter(PurchaseItem.purchase_id == doc.id).all())
        items = [prod.name for _it, prod in rows]
        bal = document_balances(s, kind, [doc.id])[doc.id]
        user = s.get(User, doc.created_by_user_id) if getattr(doc, "created_by_user_id", None) else None
        pend = _pending(s, party_id, shared, wname)
        return {
            "kind": kind, "ref": f"{_PREFIX[kind]}-{int(doc.id):05d}", "doc_id": int(doc.id),
            "party_id": party_id, "name": _party_name(party, wname),
            "phone": (party.phone or "") if party else "",
            "when": doc.completed_at or doc.created_at,
            "served_by": user.username if user else "",
            "current": {
                "items": items, "total": float(bal.net_total),
                "paid": float(min(bal.settled, bal.net_total)),
                "balance": float(bal.balance), "status": bal.status,
            },
            "pending": pend,
            "has_pending": bool(pend["they_owe_us"] or pend["we_owe_them"]),
        }

    @staticmethod
    def _advance(s, adv_id: int) -> Dict[str, Any]:
        adv = s.get(Advance, adv_id)
        if not adv:
            raise ValueError(f"Advance not found: {adv_id}")
        party_id = int(adv.party_id)
        party = s.get(Party, party_id)
        shared = is_shared_walkin(party_id, s)
        amount = float(adv.amount or 0)
        pays = (s.query(Payment).filter(Payment.entity_type == "advance", Payment.entity_id == adv.id,
                                        Payment.status != "voided")
                .order_by(Payment.created_at.asc(), Payment.id.asc()).all())
        allocs = (s.query(AdvanceAllocation).filter(AdvanceAllocation.advance_id == adv.id)
                  .order_by(AdvanceAllocation.id.asc()).all())
        history: List[Dict[str, Any]] = []
        for p in pays:
            history.append({"when": p.created_at, "amount": float(p.amount or 0),
                            "what": (p.method or "cash"), "ref": ""})
        for a in allocs:
            history.append({"when": getattr(a, "created_at", None), "amount": float(a.amount or 0),
                            "what": "against",
                            "ref": f"{'PUR' if a.entity_type == 'purchase' else 'SAL'}-{int(a.entity_id):05d}"})
        used = sum(h["amount"] for h in history)
        voided = (adv.status or "") == "voided"
        user = s.get(User, adv.created_by_user_id) if adv.created_by_user_id else None
        pend = _pending(s, party_id, shared, "")
        return {
            "kind": "advance", "ref": f"ADV-{int(adv.id):05d}", "doc_id": int(adv.id),
            "party_id": party_id, "name": _party_name(party), "phone": (party.phone or "") if party else "",
            "when": adv.created_at, "served_by": user.username if user else "",
            "current": {
                "direction": "out" if (adv.direction or "") == "given" else "in",
                "amount": amount, "used": float(min(used, amount)),
                "remaining": 0.0 if voided else max(amount - used, 0.0),
                "voided": voided, "history": history, "notes": (adv.notes or "").strip(),
            },
            "pending": pend,
            "has_pending": bool(pend["they_owe_us"] or pend["we_owe_them"]),
        }
