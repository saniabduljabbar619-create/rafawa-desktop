# database/services/weigh_service.py
"""
Weighing tickets - the Weigher workspace's backend.

WEIGHER   registers/selects a customer or supplier, picks product / area / grade, records each
          round (1-3 or partial bags + kg), SETS THE PRICE PER KG after weighing, and submits the
          ticket (a ticket cannot be submitted without a price on every line). Auto totals; prints a
          slip with the amounts. Until the cashier finalizes it, the weigher can still edit (rounds,
          prices) or remove (cancel) their own tickets - every change after submitting is audited.
ADMIN /   open the submitted ticket, verify and correct the rounds, confirm (or change) the price, and
CASHIER   finalize - which creates the purchase (stock, payable, receipt) through the normal
          PurchaseService. Payment then follows the normal purchase payment flow.

THE RULE this module enforces (not just the screens): the weigher has no authority over payment or
finalization - those calls are refused for that role. The price they set is a proposal the cashier
confirms at finalization.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, or_

from database.models.party import Party
from database.models.product import Product
from database.models.user import User
from database.models.weigh_ticket import WeighRound, WeighTicket, WeighTicketLine
from database.services.audit_service import AuditService
from database.services.walkin_account import is_shared_walkin
from database.session import get_session
from shared.auth.permissions import (
    INTAKE_ROLES, WEIGH_ROLES, PermissionDenied, current_role, require_role,
)
from shared.auth.session import get_session as get_login_session

MAX_BAGS_PER_ROUND = 3            # a weigher's round is 1-3 bags (or part of a bag)
MAX_KG_PER_ROUND = 5000.0         # sanity ceiling against a mistyped weight
STATUSES = ("open", "submitted", "finalized", "cancelled")
RECEIPT_TYPES = ("weighing", "value", "full", "summary")     # the first two are the weigher's receipts; the rest are older slips

ANY_STAFF = WEIGH_ROLES | INTAKE_ROLES


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _uid() -> int:
    sess = get_login_session()
    return int(getattr(sess, "user_id", 0) or 0)


def _is_weigher() -> bool:
    return current_role() == "weigher"


def _ticket_no(ticket_id: int) -> str:
    return f"WT-{int(ticket_id):05d}"


def _bags_whole(total_bags: float) -> int:
    """Whole bags for the purchase record (a delivery of 12.5 bags is booked as 13; admin can adjust)."""
    return max(int(Decimal(str(total_bags)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), 1)


class WeighService:

    # ------------------------------------------------------------------ parties
    @staticmethod
    @require_role(*ANY_STAFF)
    def register_party(kind: str, name: str, phone: str, address: str = "") -> Dict[str, Any]:
        """Register a customer or supplier (a phone number identifies them; no duplicates)."""
        kind = (kind or "").strip().lower()
        name = (name or "").strip()
        phone = (phone or "").strip()
        address = (address or "").strip()
        if kind not in ("customer", "supplier"):
            raise ValueError("Choose customer or supplier.")
        if len(name) < 2:
            raise ValueError("Name is required.")
        if len(phone) < 7:
            raise ValueError("A valid phone number is required.")

        with get_session() as s:
            existing = s.query(Party).filter(Party.phone == phone, Party.is_active.is_(True)).first()
            if existing is not None:
                raise ValueError(
                    f"{existing.display_name} is already registered with this phone number - select them instead.")
            p = Party(party_type=kind, display_name=name, phone=phone, address=address or None,
                      is_active=True, is_customer=(kind == "customer"), is_supplier=(kind == "supplier"))
            s.add(p)
            s.flush()
            AuditService.record("party.register", "party", int(p.id),
                                f"Registered {kind} {name!r} (weigher workspace)", session=s)
            out = {"id": int(p.id), "name": p.display_name, "phone": p.phone or "", "kind": kind}
            s.commit()
        WeighService._refresh_party_cache()
        return out

    @staticmethod
    def _refresh_party_cache() -> None:
        try:
            from apps.product_management_app.app_state import state
            state._load_customers_from_db()
        except Exception:
            pass

    @staticmethod
    @require_role(*ANY_STAFF)
    def search_parties(q: str = "", limit: int = 60, customers_only: bool = False) -> List[Dict[str, Any]]:
        """Customers and suppliers (customers only when asked), matched on name or phone."""
        q = (q or "").strip().lower()
        with get_session() as s:
            query = s.query(Party).filter(Party.is_active.is_(True),
                                          Party.is_customer.is_(True) if customers_only
                                          else or_(Party.is_customer.is_(True), Party.is_supplier.is_(True)))
            if q:
                like = f"%{q}%"
                query = query.filter(or_(func.lower(Party.display_name).like(like),
                                         func.lower(func.coalesce(Party.phone, "")).like(like)))
            rows = query.order_by(Party.display_name.asc()).limit(int(limit)).all()
            return [{
                "id": int(p.id), "name": p.display_name, "phone": p.phone or "",
                "kind": "supplier" if p.is_supplier and not p.is_customer else "customer" if p.is_customer and not p.is_supplier else "both",
            } for p in rows]

    @staticmethod
    @require_role(*ANY_STAFF)
    def search_walkins(q: str = "", limit: int = 15) -> List[str]:
        """Walk-in names used before (on weighings, sales or purchases), matched on any part of the name."""
        q = (q or "").strip().lower()
        names: Dict[str, str] = {}
        with get_session() as s:
            rows = s.query(WeighTicket.walkin_name).filter(WeighTicket.walkin_name.isnot(None)).distinct().all()
        for (n,) in rows:
            n = (n or "").strip()
            if n:
                names.setdefault(n.lower(), n)
        try:
            from database.services.walkin_service import WalkinService
            for t in WalkinService.list_traders():
                n = (t.get("name") or "").strip()
                if n:
                    names.setdefault(n.lower(), n)
        except Exception:
            pass
        out = sorted((n for k, n in names.items() if not q or q in k), key=str.lower)
        return out[:int(limit)]

    @staticmethod
    @require_role(*ANY_STAFF)
    def crop_products() -> List[Dict[str, Any]]:
        """Crops the weigher can pick (the catalog is managed by admin)."""
        with get_session() as s:
            rows = (s.query(Product).filter(Product.product_type == "crops", Product.is_active.is_(True))
                    .order_by(Product.name.asc()).all())
            return [{"id": int(p.id), "name": p.name, "sku": p.sku or "",
                     "price": _f(p.default_cost_rate)} for p in rows]          # usual price per kg, 0 = none set

    @staticmethod
    @require_role(*ANY_STAFF)
    def known_areas_and_grades() -> Dict[str, List[str]]:
        """Previously used areas/grades, to speed up typing."""
        with get_session() as s:
            areas = [r[0] for r in s.query(WeighTicketLine.area).filter(WeighTicketLine.area.isnot(None))
                     .filter(WeighTicketLine.area != "").distinct().order_by(WeighTicketLine.area).limit(100)]
            grades = [r[0] for r in s.query(WeighTicketLine.grade).filter(WeighTicketLine.grade.isnot(None))
                      .filter(WeighTicketLine.grade != "").distinct().order_by(WeighTicketLine.grade).limit(50)]
        base = ["A", "B", "C", "Mixed"]
        return {"areas": areas, "grades": base + [g for g in grades if g not in base]}

    # ------------------------------------------------------------------ guards
    @staticmethod
    def _load_ticket(s, ticket_id: int, *, lock: bool = False) -> WeighTicket:
        q = s.query(WeighTicket).filter(WeighTicket.id == int(ticket_id))
        if lock:
            q = q.with_for_update()
        t = q.first()
        if t is None:
            raise ValueError("Ticket not found.")
        return t

    @staticmethod
    def _assert_can_edit(t: WeighTicket) -> None:
        """Who may change this ticket's weighing right now."""
        if t.status in ("finalized", "cancelled"):
            raise ValueError(f"This ticket is {t.status} and can no longer be changed.")
        if _is_weigher():
            if int(t.weigher_user_id) != _uid():
                raise PermissionDenied("This is another weigher's ticket.")
            # their own ticket stays correctable until the cashier finalizes it (open, or awaiting the cashier)
            return
        if t.status == "open" and current_role() == "cashier":
            raise PermissionDenied("The weigher is still weighing this ticket - wait until it is submitted.")

    @staticmethod
    def _validate_round(bags: float, weight_kg: float) -> None:
        bags, weight_kg = _f(bags), _f(weight_kg)
        if bags <= 0:
            raise ValueError("Bags must be more than zero (use a fraction for a partial bag).")
        if _is_weigher() and bags > MAX_BAGS_PER_ROUND + 1e-9:
            raise ValueError(f"A round holds at most {MAX_BAGS_PER_ROUND} bags - record another round.")
        if bags > 1000:
            raise ValueError("That many bags in one round looks like a typing mistake.")
        if weight_kg <= 0:
            raise ValueError("Weight must be more than zero.")
        if weight_kg > MAX_KG_PER_ROUND:
            raise ValueError(f"A single round over {MAX_KG_PER_ROUND:,.0f} kg looks like a typing mistake.")

    # ------------------------------------------------------------------ weigher: ticket + rounds
    @staticmethod
    @require_role(*WEIGH_ROLES)
    def create_ticket(party_id: int, party_role: str = "supplier", walkin_name: str = "") -> int:
        party_role = (party_role or "supplier").strip().lower()
        if party_role not in ("customer", "supplier"):
            raise ValueError("party_role must be customer or supplier.")
        walkin_name = (walkin_name or "").strip()
        with get_session() as s:
            if walkin_name:                                          # a walk-in: the shared account, told apart by name
                if not is_shared_walkin(party_id, s):
                    raise ValueError("There is no shared walk-in account in this database, so a walk-in cannot be "
                                     "weighed. Register the person as a customer instead.")
                who = walkin_name
            elif is_shared_walkin(party_id, s):
                raise ValueError("Type the walk-in's name first.")
            else:
                p = s.get(Party, int(party_id))
                if p is None or not p.is_active:
                    raise ValueError("Select a registered customer or a walk-in first.")
                walkin_name, who = None, p.display_name
            t = WeighTicket(party_id=int(party_id), party_role=party_role, status="open",
                            weigher_user_id=_uid(), walkin_name=walkin_name)
            s.add(t)
            s.flush()
            t.ticket_no = _ticket_no(t.id)
            AuditService.record("weigh.create", "weigh_ticket", int(t.id),
                                f"Started {t.ticket_no} for {who!r}", session=s)
            s.commit()
            return int(t.id)

    @staticmethod
    @require_role(*WEIGH_ROLES)
    def add_line(ticket_id: int, product_id: int, area: str = "", grade: str = "") -> int:
        with get_session() as s:
            t = WeighService._load_ticket(s, ticket_id)
            WeighService._assert_can_edit(t)
            prod = s.get(Product, int(product_id))
            if prod is None or prod.product_type != "crops" or not prod.is_active:
                raise ValueError("Select a crop product.")
            order = (s.query(func.coalesce(func.max(WeighTicketLine.sort_order), 0))
                     .filter(WeighTicketLine.ticket_id == t.id).scalar() or 0) + 1
            line = WeighTicketLine(ticket_id=t.id, product_id=prod.id, sort_order=order,
                                   area=(area or "").strip() or None, grade=(grade or "").strip() or None)
            s.add(line)
            s.flush()
            s.commit()
            return int(line.id)

    @staticmethod
    @require_role(*ANY_STAFF)
    def record_round(line_id: int, bags: float, weight_kg: float) -> Dict[str, Any]:
        """Record one round. A weigher does this on their own open ticket; admin/cashier may add a
        missed round while verifying a submitted one."""
        WeighService._validate_round(bags, weight_kg)
        with get_session() as s:
            line = s.get(WeighTicketLine, int(line_id))
            if line is None:
                raise ValueError("Line not found.")
            t = WeighService._load_ticket(s, line.ticket_id)
            WeighService._assert_can_edit(t)
            nxt = (s.query(func.coalesce(func.max(WeighRound.round_no), 0))
                   .filter(WeighRound.line_id == line.id).scalar() or 0) + 1
            r = WeighRound(line_id=line.id, round_no=nxt, bags=_f(bags), weight_kg=_f(weight_kg),
                           recorded_by_user_id=_uid())
            s.add(r)
            s.flush()
            if t.status == "submitted":
                AuditService.record("weigh.round_added", "weigh_ticket", int(t.id),
                                    f"{t.ticket_no}: round {nxt} added ({_f(bags):g} bags, {_f(weight_kg):,.2f} kg)",
                                    session=s)
            out = {"id": int(r.id), "round_no": nxt, "bags": _f(bags), "weight_kg": _f(weight_kg)}
            s.commit()
            return out

    @staticmethod
    @require_role(*ANY_STAFF)
    def remove_round(round_id: int) -> None:
        with get_session() as s:
            r = s.get(WeighRound, int(round_id))
            if r is None:
                raise ValueError("Round not found.")
            line = s.get(WeighTicketLine, r.line_id)
            t = WeighService._load_ticket(s, line.ticket_id)
            WeighService._assert_can_edit(t)
            if t.status == "submitted":
                AuditService.record("weigh.round_removed", "weigh_ticket", int(t.id),
                                    f"{t.ticket_no}: round {r.round_no} removed ({_f(r.bags):g} bags, {_f(r.weight_kg):,.2f} kg)",
                                    session=s)
            s.delete(r)
            s.flush()
            # keep round numbers 1..n with no gaps
            for i, rr in enumerate(s.query(WeighRound).filter(WeighRound.line_id == line.id)
                                   .order_by(WeighRound.round_no).all(), start=1):
                rr.round_no = i
            s.commit()

    @staticmethod
    @require_role(*ANY_STAFF)
    def remove_line(line_id: int) -> None:
        with get_session() as s:
            line = s.get(WeighTicketLine, int(line_id))
            if line is None:
                return
            t = WeighService._load_ticket(s, line.ticket_id)
            WeighService._assert_can_edit(t)
            s.delete(line)
            s.commit()

    @staticmethod
    @require_role(*ANY_STAFF)
    def update_round(round_id: int, bags: Optional[float] = None, weight_kg: Optional[float] = None) -> None:
        """Correct a round: the weigher on their own still-open ticket, admin/cashier while verifying."""
        with get_session() as s:
            r = s.get(WeighRound, int(round_id))
            if r is None:
                raise ValueError("Round not found.")
            line = s.get(WeighTicketLine, r.line_id)
            t = WeighService._load_ticket(s, line.ticket_id)
            WeighService._assert_can_edit(t)
            new_bags = _f(bags) if bags is not None else _f(r.bags)
            new_kg = _f(weight_kg) if weight_kg is not None else _f(r.weight_kg)
            WeighService._validate_round(new_bags, new_kg)
            if abs(new_bags - _f(r.bags)) > 1e-9 or abs(new_kg - _f(r.weight_kg)) > 1e-9:
                AuditService.record(
                    "weigh.round_edited", "weigh_ticket", int(t.id),
                    f"{t.ticket_no} round {r.round_no}: {_f(r.bags):g} bags/{_f(r.weight_kg):,.2f} kg -> "
                    f"{new_bags:g} bags/{new_kg:,.2f} kg", session=s)
                r.bags, r.weight_kg, r.edited_by_user_id = new_bags, new_kg, _uid()
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def set_line_area_grade(line_id: int, area: str = "", grade: str = "") -> None:
        with get_session() as s:
            line = s.get(WeighTicketLine, int(line_id))
            if line is None:
                raise ValueError("Line not found.")
            WeighService._assert_can_edit(WeighService._load_ticket(s, line.ticket_id))
            line.area = (area or "").strip() or None
            line.grade = (grade or "").strip() or None
            s.commit()

    @staticmethod
    @require_role(*WEIGH_ROLES)
    def submit_ticket(ticket_id: int, receipt_type: str = "weighing") -> Dict[str, Any]:
        """Weigher is done: lock the ticket and hand it to the cashier/admin."""
        receipt_type = (receipt_type or "weighing").strip().lower()
        if receipt_type not in RECEIPT_TYPES:
            raise ValueError("Choose a receipt type.")
        with get_session() as s:
            t = WeighService._load_ticket(s, ticket_id, lock=True)
            WeighService._assert_can_edit(t)
            if t.status != "open":
                raise ValueError("This ticket was already submitted.")
            empties = [ln for ln in t.lines if not ln.rounds]
            for ln in empties:
                s.delete(ln)
            s.flush()
            if len(empties) == len(t.lines) or not t.lines:
                raise ValueError("Record at least one round before finishing.")
            unpriced = [ln.product.name if ln.product else "a product" for ln in t.lines
                        if ln.rounds and _f(ln.unit_price) <= 0]
            if unpriced:
                raise ValueError("Set the price per kg before submitting: " + ", ".join(unpriced) + ".")
            t.status = "submitted"
            t.submitted_at = datetime.now()
            t.receipt_type = receipt_type
            tb, tk = WeighService._totals(t)
            AuditService.record("weigh.submit", "weigh_ticket", int(t.id),
                                f"{t.ticket_no} submitted: {tb:g} bags, {tk:,.2f} kg", session=s)
            s.commit()
        return WeighService.get_ticket(ticket_id)

    @staticmethod
    @require_role(*ANY_STAFF)
    def cancel_ticket(ticket_id: int, reason: str = "") -> None:
        with get_session() as s:
            t = WeighService._load_ticket(s, ticket_id, lock=True)
            if t.status in ("finalized", "cancelled"):
                raise ValueError(f"This ticket is already {t.status}.")
            if _is_weigher() and int(t.weigher_user_id) != _uid():
                raise PermissionDenied("Only your own tickets can be removed.")
            t.status = "cancelled"
            t.cancelled_by_user_id = _uid()
            t.cancel_reason = (reason or "").strip()[:255] or None
            AuditService.record("weigh.cancel", "weigh_ticket", int(t.id),
                                f"{t.ticket_no} cancelled" + (f": {t.cancel_reason}" if t.cancel_reason else ""),
                                session=s)
            s.commit()

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _totals(t: WeighTicket):
        bags = sum(_f(r.bags) for ln in t.lines for r in ln.rounds)
        kg = sum(_f(r.weight_kg) for ln in t.lines for r in ln.rounds)
        return bags, kg

    @staticmethod
    def _ticket_dict(s, t: WeighTicket, *, with_prices: bool) -> Dict[str, Any]:
        users = {}

        def uname(uid):
            if not uid:
                return ""
            if uid not in users:
                u = s.get(User, int(uid))
                users[uid] = u.username if u else ""
            return users[uid]

        lines, total_amt = [], 0.0
        for ln in t.lines:
            kg = sum(_f(r.weight_kg) for r in ln.rounds)
            bags = sum(_f(r.bags) for r in ln.rounds)
            price = _f(ln.unit_price) if ln.unit_price is not None else None
            amount = round(kg * price, 2) if (with_prices and price is not None) else None
            if amount:
                total_amt += amount
            lines.append({
                "id": int(ln.id), "product_id": int(ln.product_id),
                "product_name": ln.product.name if ln.product else "", "sku": (ln.product.sku if ln.product else "") or "",
                "area": ln.area or "", "grade": ln.grade or "",
                "bags": bags, "kg": kg,
                "unit_price": price if with_prices else None,
                "amount": amount,
                "rounds": [{"id": int(r.id), "round_no": int(r.round_no), "bags": _f(r.bags),
                            "weight_kg": _f(r.weight_kg), "created_at": r.created_at,
                            "edited": bool(r.edited_by_user_id)} for r in ln.rounds],
            })
        tb, tk = WeighService._totals(t)
        payment = {"status": "Unpaid", "paid": 0.0, "balance": round(total_amt, 2), "total": round(total_amt, 2)}
        if with_prices and t.purchase_id:                       # finalized: the real purchase's position
            from database.models.purchase import Purchase
            from database.services.purchase_adjustment_service import PurchaseAdjustmentService
            pur = s.get(Purchase, int(t.purchase_id))
            if pur is not None:
                n = PurchaseAdjustmentService._purchase_balance(s, pur)
                payment = {"status": "Paid" if n["balance"] <= 0.005 else ("Part paid" if n["paid"] > 0.005 else "Unpaid"),
                           "paid": round(n["paid"], 2), "balance": round(n["balance"], 2),
                           "total": round(n["net_total"], 2)}
        return {
            "payment": payment,
            "id": int(t.id), "ticket_no": t.ticket_no or _ticket_no(t.id), "status": t.status,
            "party_id": int(t.party_id), "party_name": t.walkin_name or (t.party.display_name if t.party else ""),
            "walkin_name": t.walkin_name or "",
            "party_phone": (t.party.phone if t.party else "") or "", "party_role": t.party_role,
            "weigher_user_id": int(t.weigher_user_id), "weigher_name": uname(t.weigher_user_id),
            "created_at": t.created_at, "submitted_at": t.submitted_at, "receipt_type": t.receipt_type or "weighing",
            "notes": t.notes or "", "purchase_id": int(t.purchase_id) if t.purchase_id else None,
            "finalized_at": t.finalized_at, "finalized_by": uname(t.finalized_by_user_id),
            "cancel_reason": t.cancel_reason or "",
            "total_bags": tb, "total_kg": tk,
            "total_amount": round(total_amt, 2) if with_prices else None,
            "lines": lines,
        }

    @staticmethod
    @require_role(*ANY_STAFF)
    def get_ticket(ticket_id: int) -> Dict[str, Any]:
        with get_session() as s:
            t = WeighService._load_ticket(s, ticket_id)
            if _is_weigher() and int(t.weigher_user_id) != _uid():
                raise PermissionDenied("This is another weigher's ticket.")
            return WeighService._ticket_dict(s, t, with_prices=True)

    @staticmethod
    @require_role(*ANY_STAFF)
    def list_tickets(status: Optional[Iterable[str]] = None, mine_only: bool = False,
                     limit: int = 300) -> List[Dict[str, Any]]:
        """Summaries, newest first (with amounts). A weigher only ever sees their own tickets."""
        statuses = [x for x in ([status] if isinstance(status, str) else list(status or [])) if x in STATUSES]
        with get_session() as s:
            q = s.query(WeighTicket)
            if statuses:
                q = q.filter(WeighTicket.status.in_(statuses))
            if mine_only or _is_weigher():
                q = q.filter(WeighTicket.weigher_user_id == _uid())
            tickets = q.order_by(WeighTicket.id.desc()).limit(int(limit)).all()
            out = []
            for t in tickets:
                d = WeighService._ticket_dict(s, t, with_prices=True)
                d.pop("lines", None)
                d["products"] = ", ".join(sorted({ln.product.name for ln in t.lines if ln.product}))
                out.append(d)
            return out

    @staticmethod
    @require_role(*ANY_STAFF)
    def count_awaiting() -> int:
        with get_session() as s:
            return int(s.query(func.count(WeighTicket.id)).filter(WeighTicket.status == "submitted").scalar() or 0)

    # ------------------------------------------------------------------ admin / cashier: price + finalize
    @staticmethod
    def _price_line(s, line: WeighTicketLine, price: float) -> None:
        t = WeighService._load_ticket(s, line.ticket_id)
        if t.status in ("finalized", "cancelled"):
            raise ValueError(f"This ticket is {t.status}.")
        if _is_weigher():
            WeighService._assert_can_edit(t)               # their own ticket only
        if _f(line.unit_price) != price:
            AuditService.record("weigh.price", "weigh_ticket", int(t.id),
                                f"{t.ticket_no}: {line.product.name if line.product else 'line'} priced at {price:,.2f}/kg",
                                session=s)
        line.unit_price = price

    @staticmethod
    @require_role(*ANY_STAFF)
    def set_line_price(line_id: int, unit_price: float) -> None:
        """Price per kg for one line: the weigher after weighing (own ticket), or admin/cashier at review."""
        price = _f(unit_price)
        if price <= 0:
            raise ValueError("Price per kg must be more than zero.")
        with get_session() as s:
            line = s.get(WeighTicketLine, int(line_id))
            if line is None:
                raise ValueError("Line not found.")
            WeighService._price_line(s, line, price)
            s.commit()

    @staticmethod
    @require_role(*ANY_STAFF)
    def set_ticket_prices(ticket_id: int, prices: Dict[int, float]) -> None:
        """Price every line of a ticket at once ({line_id: price per kg}); nothing is saved unless all are valid."""
        prices = {int(k): _f(v) for k, v in (prices or {}).items()}
        with get_session() as s:
            t = WeighService._load_ticket(s, ticket_id)
            lines = [ln for ln in t.lines if ln.rounds]
            missing = [ln.product.name if ln.product else "a product" for ln in lines
                       if prices.get(int(ln.id), 0) <= 0]
            if missing:
                raise ValueError("Enter a price per kg for: " + ", ".join(missing) + ".")
            for ln in lines:
                WeighService._price_line(s, ln, prices[int(ln.id)])
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def finalize_ticket(ticket_id: int, prices: Optional[Dict[int, float]] = None) -> Dict[str, Any]:
        """Confirm prices and turn the ticket into a real purchase (stock in, payable raised).
        Payment is taken afterwards through the normal purchase payment screen."""
        from database.services.purchase_service import PurchaseService

        prices = {int(k): _f(v) for k, v in (prices or {}).items()}
        with get_session() as s:
            t = WeighService._load_ticket(s, ticket_id, lock=True)       # row lock: no double-finalize
            if t.status != "submitted":
                raise ValueError(
                    "Only a submitted ticket can be finalized (this one is "
                    f"{'already finalized' if t.status == 'finalized' else t.status}).")
            items, note_bits = [], []
            for ln in t.lines:
                if not ln.rounds:
                    continue
                price = prices.get(int(ln.id), _f(ln.unit_price))
                if price <= 0:
                    raise ValueError(f"Confirm a price per kg for {ln.product.name if ln.product else 'every line'} first.")
                ln.unit_price = price
                bags = sum(_f(r.bags) for r in ln.rounds)
                kg = sum(_f(r.weight_kg) for r in ln.rounds)
                tag = " / ".join(x for x in (ln.area, ln.grade and f"Grade {ln.grade}") if x)
                items.append({
                    "sku": ln.product.sku or "", "product_name": ln.product.name, "product_type": "crops",
                    "qty_bags": _bags_whole(bags), "total_weight_kg": round(kg, 2), "unit_cost": price,
                    "note": (f"{t.ticket_no}" + (f" - {tag}" if tag else ""))[:255],
                })
            if not items:
                raise ValueError("This ticket has nothing to finalize.")

            res = PurchaseService.record({
                "supplier_party_id": int(t.party_id),
                "created_by_user_id": _uid(),
                "items": items,
                "note": f"Weigh ticket {t.ticket_no} (weighed by {s.get(User, t.weigher_user_id).username})",
                "walkin_name": t.walkin_name or "",
                "auto_flag_supplier": t.party_role == "supplier",
            })
            t.status = "finalized"
            t.purchase_id = int(res["purchase_id"])
            t.finalized_by_user_id = _uid()
            t.finalized_at = datetime.now()
            AuditService.record("weigh.finalize", "weigh_ticket", int(t.id),
                                f"{t.ticket_no} finalized as purchase #{res['purchase_id']} "
                                f"({_f(res.get('total_amount')):,.2f})", session=s)
            s.commit()
            return {"purchase_id": int(res["purchase_id"]), "total_amount": _f(res.get("total_amount")),
                    "party_id": int(t.party_id)}
