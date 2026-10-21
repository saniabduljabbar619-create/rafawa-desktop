# apps/product_management_app/app_state.py
from dataclasses import dataclass
from typing import List, Optional

from shared.config.constants import LEDGER_CROPS, LEDGERS

from database.session import get_session
from database.models.party import Party
from datetime import datetime
from database.services.product_service import adjust_product_inventory_db
from database.models.product import Product as DBProduct
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.stock_movement import StockMovement
from sqlalchemy import desc

from PySide2.QtCore import QObject, Signal

class AppEvents(QObject):
    """
    Tiny process-wide event bus, separate from AppState itself because
    AppState is a plain object (not a QObject) and can't host Signals
    directly. Any screen whose numbers are derived from Payment/Sale/
    Purchase/Advance rows can subscribe to money_changed and re-run its
    own compute() when something elsewhere (e.g. an admin voiding a
    payment from inside a different screen entirely) invalidates it.

    Deliberately unscoped (no user_id/entity payload) - at this app's
    volume a redundant recompute is cheap, and a scoped signal is one
    more thing every future money-mutating call site has to get right.
    """
    money_changed = Signal()


@dataclass
class Product:
    id: int
    name: str
    sku: str
    category: str

    cost_price: float     # purchase/supply cost
    sale_price: float     # selling price (cashier-controlled)

    stock: int
    status: str

    @property
    def price(self) -> float:
        # Backward-compat for UI screens that still expect p.price
        return float(self.sale_price if self.sale_price is not None else self.cost_price)


@dataclass
class Customer:
    id: int
    full_name: str
    phone: str
    email: str
    address: str
    status: str   # active/inactive
    notes: str
    is_supplier: bool = False
    code: str = ""          # walk-ins only: WKN-00001 / WT-WKN-00001


@dataclass
class ActivityRecord:
    id: int
    customer_id: int
    dt: str
    ledger: str          # crops / fertilizers

    type: str
    reference: str
    summary: str
    amount: float
    status: str
    meta: dict


class AppState:
    def __init__(self):
        self.current_user_id = None
        self.products: List[Product] = []
        self._next_id = 1

        self.customers: List[Customer] = []
        self.walkins: List[Customer] = []  # 🚀 NEW: Isolated state array cache for casual walk-ins
        self._next_customer_id = 1
        self.product_pack_kg = {}  # product_id -> dict(pack_name -> kg_per_bag)

        self.activities = []          # list[ActivityRecord]
        self._next_activity_id = 1
        self._dlv_seq = 1
        self._pay_seq = 1
        self._sale_seq = 1
        self.product_variants = {}  # product_id -> list[str]
        self.session_user = {
                "id": 0,
                "name": "",
                "role": "",
            }
        
        # Hydrate state tables safely on core startup initialization
        try:
            self._load_customers_from_db()
            self._load_walkins_from_db()  # 🚀 NEW: Load casual profiles into their own lane
        except Exception:
            self.customers = []
            self.walkins = []

    def adjust_product_inventory_db(self, product_id, bags_diff, weight_diff):
        return adjust_product_inventory_db(
            product_id=product_id,
            bags_diff=bags_diff,
            weight_diff=weight_diff,
            user_id=self.session_user["id"]
        )

    def _load_customers_from_db(self) -> None:
        """
        Refresh in-memory state.customers list from DB parties table.
        Keeps UI unchanged because CustomersListView reads state.customers directly.
        """
        with get_session() as s:
            rows = (
                s.query(Party)
                .filter(Party.is_customer == True)  # noqa: E712
                .filter(Party.party_type != "walkin")            # a walk-in is never a registered customer
                .filter(Party.is_active == True)  # noqa: E712
                .order_by(Party.id.desc())
                .all()
            )

        # convert DB -> UI dataclass
        self.customers = [
            Customer(
                id=int(r.id),
                full_name=str(r.display_name or ""),
                phone=str(r.phone or ""),
                email="",          # DB Party doesn't have email in your models
                address=str(r.address or ""),
                status="active" if bool(r.is_active) else "inactive",
                notes="",          # Party model doesn't have notes (yet)
                is_supplier=bool(r.is_supplier),
            )
            for r in rows
        ]

    def _load_walkins_from_db(self) -> None:
        """
        🚀 NEW: Refresh in-memory state.walkins list from DB parties table.
        Excludes formal customers, targeting only the casual trader profiles.
        """
        with get_session() as s:
            rows = (
                s.query(Party)
                .filter(Party.party_type == "walkin")
                .filter(Party.is_active == True)  # noqa: E712
                .order_by(Party.id.desc())
                .all()
            )

        self.walkins = [
            Customer(
                id=int(r.id),
                full_name=str(r.display_name or ""),
                phone=str(r.phone or ""),
                email="",
                address=str(r.address or "Walk-in Client"),
                status="active" if bool(r.is_active) else "inactive",
                notes="",
                code=str(r.walkin_code or ""),
            )
            for r in rows
        ]

    def bootstrap_db_state(self) -> None:
        """
        Call once on startup.
        """
        self._load_customers_from_db()
        self._load_walkins_from_db()  # 🚀 Synchronize both caches concurrently

    def create_product(self, name, sku, category, cost_price, sale_price, stock, status):
        p = Product(
            self._next_id,
            str(name).strip(),
            str(sku).strip(),
            str(category).strip(),
            float(cost_price),
            float(sale_price),
            int(stock),
            str(status).strip().lower(),
        )
        self._next_id += 1
        self.products.append(p)
        return p

    def sku_exists(self, sku: str, exclude_id: Optional[int] = None) -> bool:
        s = sku.strip().lower()
        for p in self.products:
            if exclude_id is not None and p.id == exclude_id:
                continue
            if p.sku.strip().lower() == s:
                return True
        return False
    
    def find_product_by_id(self, pid: int):
        pid = int(pid)
        return next((p for p in self.products if p.id == pid), None)
    
    def create_customer(self, full_name, phone, email="", address="", status="active", notes=""):
        with get_session() as s:
            c = Party(
                party_type="customer",
                display_name=str(full_name).strip(),
                phone=str(phone).strip(),
                address=str(address).strip(),
                is_active=(str(status).strip().lower() != "inactive"),
                # Without this, _load_customers_from_db() (which filters on
                # is_customer, not party_type) never sees a customer created
                # through THIS screen - confirmed root cause of the "new
                # customers invisible" bug.
                is_customer=True,
            )
            s.add(c)
            s.flush()  # get id

            new_id = int(c.id)

        # refresh cache for UI
        self._load_customers_from_db()

        # return the created Customer object (from cache)
        return next((x for x in self.customers if x.id == new_id), None)

    def update_customer(self, customer_id: int, **fields):
        cid = int(customer_id)
        with get_session() as s:
            row = s.query(Party).filter(Party.id == cid).first()
            if not row:
                raise ValueError("Party profile record not found")

            if "full_name" in fields:
                row.display_name = str(fields["full_name"]).strip()
            if "phone" in fields:
                row.phone = str(fields["phone"]).strip()
            if "address" in fields:
                row.address = str(fields["address"]).strip()
            if "status" in fields:
                row.is_active = (str(fields["status"]).strip().lower() != "inactive")

        self._load_customers_from_db()
        self._load_walkins_from_db()  # Refresh both matrices defensively
        
        # Attempt to return the matched entry from whichever list it belongs to
        return next((x for x in self.customers + self.walkins if x.id == cid), None)

    def delete_customer(self, customer_id: int):
        cid = int(customer_id)
        with get_session() as s:
            row = s.query(Party).filter(Party.id == cid).first()
            if row:
                row.is_active = False
        self._load_customers_from_db()
        self._load_walkins_from_db()

    def customer_phone_exists(self, phone: str, exclude_id: Optional[int] = None) -> bool:
        p = (phone or "").strip()
        if not p:
            return False

        with get_session() as s:
            q = s.query(Party.id).filter(Party.phone == p).filter(Party.is_active == True)  # noqa: E712
            q = q.filter(Party.party_type == "customer")

            if exclude_id is not None:
                q = q.filter(Party.id != int(exclude_id))
            return s.query(q.exists()).scalar()

    def next_reference(self, typ: str) -> str:
        t = (typ or "").strip().lower()

        if t in ("delivery/intake", "delivery", "intake"):
            ref = f"DLV-{self._dlv_seq:04d}"
            self._dlv_seq += 1
            return ref

        if t == "payment":
            ref = f"PAY-{self._pay_seq:04d}"
            self._pay_seq += 1
            return ref

        if t == "sale":
            ref = f"INV-{self._sale_seq:04d}"
            self._sale_seq += 1
            return ref

        ref = f"ACT-{self._next_activity_id:04d}"
        return ref

    def add_activity(
        self,
        customer_id: int,
        typ: str,
        dt: str,
        summary: str,
        amount: float,
        status: str,
        meta: Optional[dict] = None,
        ledger: str = LEDGER_CROPS,
    ) -> ActivityRecord:
        customer_id = int(customer_id or 0)
        m = dict(meta or {})
        m.setdefault("processed_by", self.session_user.get("name", ""))
        m.setdefault("processed_role", self.session_user.get("role", ""))

        rec = ActivityRecord(
            id=self._next_activity_id,
            customer_id=int(customer_id),
            dt=str(dt),
            ledger=str(ledger or LEDGER_CROPS).strip().lower(),
            type=str(typ),
            reference=self.next_reference(typ),
            summary=str(summary),
            amount=float(amount),
            status=str(status),
            meta=m,
        )
        self._next_activity_id += 1
        self.activities.append(rec)
        return rec

    def list_activities(self, customer_id: int):
        cid = int(customer_id)
        out = []
        for a in self.activities:
            if a.customer_id != cid:
                continue
            if not getattr(a, "ledger", None):
                a.ledger = LEDGER_CROPS
            out.append(a)
        return out
    
    def list_variants(self, product_id: int):
        return list(self.product_variants.get(int(product_id), []))

    def add_variant(self, product_id: int, name: str):
        pid = int(product_id)
        v = (name or "").strip()
        if not v:
            return
        items = self.product_variants.setdefault(pid, [])
        if v not in items:
            items.append(v)

    def set_session_user(self, user_id: int, name: str, role: str = "cashier") -> None:
        uid = int(user_id or 0)
        if uid <= 0:
            raise ValueError("Invalid user_id for session.")
        self.session_user["id"] = uid
        self.session_user["name"] = (name or "").strip()
        self.session_user["role"] = (role or "cashier").strip().lower()

    def clear_session(self) -> None:
        self.session_user = {"id": 0, "name": "", "role": ""}

    def find_product_by_sku(self, sku: str):
        s = (sku or "").strip().lower()
        return next((p for p in self.products if p.sku.strip().lower() == s), None)

    def upsert_product_from_intake(
        self,
        name: str,
        sku: str,
        category: str,
        unit_cost: float,
        qty_added: int,
        status: str = "active",
        sale_price: Optional[float] = None,
    ):
        p = self.find_product_by_sku(sku)
        added = int(qty_added)

        if p:
            old_stock = int(p.stock)
            old_cost = float(p.cost_price)

            new_stock = old_stock + added
            if new_stock > 0:
                p.cost_price = ((old_stock * old_cost) + (added * float(unit_cost))) / new_stock
            p.stock = new_stock

            if sale_price is not None:
                p.sale_price = float(sale_price)

            p.name = str(name).strip() or p.name
            p.category = str(category).strip() or p.category
            p.status = str(status).strip().lower()
            return p, "updated"

        # new product
        init_sale = float(sale_price) if sale_price is not None else float(unit_cost)
        p = self.create_product(name, sku, category, float(unit_cost), init_sale, added, status)
        return p, "created"

    def list_intake_activities(self):
        out = []
        for a in self.activities:
            t = str(getattr(a, "type", "")).strip().lower()
            if t in ("delivery/intake", "delivery", "intake"):
                out.append(a)
        out.sort(key=lambda x: x.dt, reverse=True)
        return out

    def _skuify(self, text: str) -> str:
        t = "".join(ch.upper() if ch.isalnum() else "-" for ch in (text or "").strip())
        while "--" in t:
            t = t.replace("--", "-")
        return t.strip("-") or "ITEM"

    def make_intake_sku(self, intake_type: str, name: str, pack: str = "") -> str:
        it = (intake_type or "").strip().lower()
        base = self._skuify(name)
        if it == "crops":
            return f"CROP-{base}"
        if it == "fertilizer":
            pk = self._skuify(pack) if pack else ""
            return f"FERT-{base}" + (f"-{pk}" if pk else "")
        return f"ITEM-{base}"
    
    def list_packs(self, product_id: int):
        pid = int(product_id)
        m = self.product_pack_kg.get(pid, {})
        return [(k, float(v)) for k, v in m.items()]

    def upsert_pack(self, product_id: int, pack_name: str, kg_per_bag: float):
        pid = int(product_id)
        name = (pack_name or "").strip()
        if not name:
            return
        kg = float(kg_per_bag)
        if kg <= 0:
            return
        m = self.product_pack_kg.setdefault(pid, {})
        m[name] = kg

    def record_intake_db(self, p: dict) -> int:
        """
        Accepts UI payloads, handles variable party key names defensively,
        and safely passes them down to the database record layers.
        """
        from database.services.purchase_service import PurchaseService
        p = dict(p or {})
        p.setdefault("created_by_user_id", self.require_user_id())

        def _s(x: object) -> str: return str(x or "").strip()
        def _f(x: object) -> float:
            try: return float(x or 0.0)
            except Exception: return 0.0
        def _i(x: object) -> int:
            try: return int(x or 0)
            except Exception: return 0

        def _ensure_tag(note: str, k: str, v: str) -> str:
            note = (note or "").strip()
            kv = f"{k}={v}"
            if kv.lower() in note.lower():
                return note
            if not note:
                return kv
            if not note.endswith(";"):
                note = note + ";"
            return f"{kv}; {note}".strip()

        def _extract_party_id(d: dict) -> int:
            return (_i(d.get("supplier_party_id")) or 
                    _i(d.get("source_party_id")) or 
                    _i(d.get("party_id")) or 
                    _i(d.get("customer_id")))

        is_canonical = bool(p.get("product_name") or p.get("intake_type") or p.get("supplier_party_id"))

        if is_canonical:
            intake_type = _s(p.get("intake_type")).lower()
            if intake_type not in ("crops", "fertilizer"):
                ledger = _s(p.get("ledger")).lower()
                intake_type = "fertilizer" if ledger in ("fertilizers", "fertilizer") else "crops"

            product_name = _s(p.get("product_name"))
            sku = _s(p.get("sku"))
            unit = _s(p.get("unit") or ("bag" if intake_type == "fertilizer" else "kg")).lower()
            qty = _f(p.get("qty"))
            unit_cost = _f(p.get("unit_cost"))

            qty_bags = _i(p.get("qty_bags") or p.get("bags"))
            if qty_bags <= 0:
                raise ValueError("qty_bags (bags) must be > 0")

            supplier_party_id = _extract_party_id(p)
            if supplier_party_id <= 0:
                supplier_party_id = _i(self.get_internal_supplier_party_id())

            total_weight_kg = p.get("total_weight_kg")
            if total_weight_kg is None:
                total_weight_kg = qty
            total_weight_kg = _f(total_weight_kg)

            note = _ensure_tag(_s(p.get("note")), "type", intake_type)

            if intake_type == "fertilizer":
                unit = "bag"
                qty = float(qty_bags)
                total_weight_kg = 0.0
            else:
                unit = "kg"
                if total_weight_kg <= 0:
                    raise ValueError("total_weight_kg must be > 0 for crops.")
                qty = float(total_weight_kg)

            db_payload = {
                "intake_type": intake_type,
                "product_name": product_name,
                "sku": sku,
                "unit": unit,
                "qty": qty,
                "total_weight_kg": total_weight_kg,
                "qty_bags": qty_bags,
                "unit_cost": float(unit_cost),
                "unit_sale_price": p.get("unit_sale_price"),
                "note": note,
                "supplier_party_id": int(supplier_party_id),
                "created_by_user_id": int(p.get("created_by_user_id") or 0),
                "auto_flag_supplier": p.get("auto_flag_supplier", True),
            }

        else:
            ledger = _s(p.get("ledger")).lower()
            intake_type = "fertilizer" if ledger in ("fertilizers", "fertilizer") else "crops"

            supplier_party_id = _extract_party_id(p)
            if supplier_party_id <= 0:
                supplier_party_id = _i(self.get_internal_supplier_party_id())

            product_name = _s(p.get("name"))
            sku = _s(p.get("sku"))

            qty_bags = _i(p.get("qty_bags") or p.get("bags"))
            if qty_bags <= 0:
                raise ValueError("qty_bags (bags) must be > 0")

            unit_cost = _f(p.get("price"))
            note = _ensure_tag(_s(p.get("note")), "type", intake_type)

            if intake_type == "fertilizer":
                unit = "bag"
                qty = float(qty_bags)
                total_weight_kg = 0.0
            else:
                unit = "kg"
                total_weight_kg = _f(p.get("total_weight_kg") or p.get("total_kg") or p.get("qty_added"))
                if total_weight_kg <= 0:
                    raise ValueError("total_weight_kg must be > 0 for crops.")
                qty = float(total_weight_kg)

            db_payload = {
                "intake_type": intake_type,
                "product_name": product_name,
                "sku": sku,
                "unit": unit,
                "qty": qty,
                "total_weight_kg": total_weight_kg,
                "qty_bags": qty_bags,
                "unit_cost": float(unit_cost),
                "unit_sale_price": None,
                "note": note,
                "supplier_party_id": int(supplier_party_id),
                "created_by_user_id": int(p.get("created_by_user_id") or 0),
                "auto_flag_supplier": p.get("auto_flag_supplier", True),
            }

        if not _s(db_payload.get("product_name")):
            raise ValueError("Product name is required.")
        if not _s(db_payload.get("sku")):
            raise ValueError("SKU is required.")

        t = _s(db_payload.get("intake_type")).lower()
        if t != "fertilizer":
            if _f(db_payload.get("total_weight_kg")) <= 0:
                raise ValueError("total_weight_kg must be > 0.")
        else:
            db_payload["total_weight_kg"] = 0.0
            db_payload["unit"] = "bag"
            db_payload["qty"] = float(_i(db_payload.get("qty_bags")))

        if _i(db_payload.get("qty_bags")) <= 0:
            raise ValueError("qty_bags (bags) must be > 0.")
        if _f(db_payload.get("unit_cost")) < 0:
            raise ValueError("Unit price cannot be negative.")

        uid = _i(db_payload.get("created_by_user_id"))
        if uid <= 0:
            raise ValueError("created_by_user_id is required (session user).")

        res = PurchaseService.record_intake(db_payload)

        if isinstance(res, dict):
            rid = 0
            for k in ("purchase_id", "intake_id", "id"):
                if k in res and _i(res.get(k)) > 0:
                    rid = _i(res.get(k))
                    break
            res["_id"] = rid
            return res

        if isinstance(res, int):
            return {"_id": int(res)}

        raise RuntimeError(f"record_intake_db() did not return a usable result: {res!r}")

    def record_intake_multi_db(self, header: dict, items: list) -> dict:
        """
        Cart checkout: records ONE purchase carrying MANY product lines
        (Masar, Dawa, Wake, Waken Soya, ... all under the same source, in
        one transaction) instead of one purchase per product.

        record_intake_db() above always wraps PurchaseService.record()
        with a single-item list even though record() itself has always
        supported a real multi-item "items" list - this is that same
        primitive, used for real, for the Intake Cart flow.

        `header` carries the transaction-level fields (supplier_party_id,
        created_by_user_id, note, walkin_name, auto_flag_supplier).
        `items` is the cart: a list of dicts, each shaped like the lines
        IntakeCartDialog.items() produces (product_name, sku,
        product_type/intake_type, qty_bags, total_weight_kg, unit_cost).
        """
        from database.services.purchase_service import PurchaseService

        def _s(x: object) -> str: return str(x or "").strip()
        def _f(x: object) -> float:
            try: return float(x or 0.0)
            except Exception: return 0.0
        def _i(x: object) -> int:
            try: return int(x or 0)
            except Exception: return 0

        h = dict(header or {})
        supplier_party_id = _i(h.get("supplier_party_id"))
        if supplier_party_id <= 0:
            raise ValueError("supplier_party_id is required.")

        created_by_user_id = _i(h.get("created_by_user_id")) or self.require_user_id()

        norm_items = []
        for raw in (items or []):
            it = dict(raw or {})

            raw_type = _s(it.get("product_type") or it.get("intake_type")).lower()
            product_type = "fertilizer" if raw_type in ("fertilizer", "fertilizers", "fert") else "crops"

            product_name = _s(it.get("product_name") or it.get("product"))
            if not product_name:
                raise ValueError("Every cart line needs a product name.")

            sku = _s(it.get("sku")) or self.make_intake_sku(product_type, product_name)

            qty_bags = _i(it.get("qty_bags") or it.get("bags"))
            if qty_bags <= 0:
                raise ValueError(f"{product_name}: bags must be > 0.")

            unit_cost = _f(it.get("unit_cost") or it.get("rate_per_kg") or it.get("rate"))
            if unit_cost <= 0:
                raise ValueError(f"{product_name}: rate must be > 0.")

            if product_type == "fertilizer":
                total_weight_kg = 0.0
            else:
                total_weight_kg = _f(it.get("total_weight_kg") or it.get("total_kg"))
                if total_weight_kg <= 0:
                    raise ValueError(f"{product_name}: total KG must be > 0.")

            norm_items.append({
                "sku": sku,
                "product_name": product_name,
                "product_type": product_type,
                "qty_bags": qty_bags,
                "total_weight_kg": total_weight_kg,
                "unit_cost": unit_cost,
            })

        if not norm_items:
            raise ValueError("Cart is empty - add at least one product.")

        purchase_payload = {
            "supplier_party_id": supplier_party_id,
            "created_by_user_id": created_by_user_id,
            "items": norm_items,
            "note": _s(h.get("note")),
            "walkin_name": _s(h.get("walkin_name")),
            "auto_flag_supplier": h.get("auto_flag_supplier", True),
        }

        result = PurchaseService.record(purchase_payload)

        # Same convention as record_intake()/PurchaseService.record_intake:
        # advances are applied only via the explicit advance-choice prompt
        # at payment time, never automatically at creation - so this is a
        # placeholder, not a real balance computation. Present so callers
        # that gate on "is there anything left to collect" (e.g.
        # CustomerProfileView.new_activity's `if remaining > 0`) work the
        # same whether the purchase came from the single-item or the
        # multi-item (cart) path.
        result["advance_used"] = 0.0
        result["remaining_payment"] = result["total_amount"]
        result["allocations"] = []

        return result

    def list_customer_intakes_db(self, customer_id: int, q: str = "", session_user_id: int = None) -> list[dict]:
        from database.services.intake_service import list_customer_intakes_db
        if session_user_id is None:
            session_user_id = self.require_user_id()

        return list_customer_intakes_db(
            customer_id=int(customer_id),
            q=q,
            session_user_id=int(session_user_id),
        )
    
    def list_customer_activities_db(
        self,
        customer_id: int,
        session_user_id: int,
        role: str,
        limit: int = 500
    ) -> list[dict]:
        from database.services.activity_service import list_customer_activities_db

        uid = int(session_user_id or 0)
        if uid <= 0:
            raise ValueError("session_user_id is required (login session).")

        return list_customer_activities_db(
            customer_id=int(customer_id),
            session_user_id=uid,
            role=str(role or ""),
            limit=int(limit or 500),
        )

    def get_internal_supplier_party_id(self) -> int:
        with get_session() as s:
            row = (
                s.query(Party)
                .filter(Party.party_type == "supplier")
                .filter(Party.display_name == "INTERNAL SUPPLY")
                .first()
            )
            if row:
                return int(row.id)

            row = Party(
                party_type="supplier",
                display_name="INTERNAL SUPPLY",
                phone="",
                address="",
                is_active=True,
            )
            s.add(row)
            s.flush()
            new_id = int(row.id)
            s.commit()
            return new_id
        
    def db_probe_insert_product(self) -> int:
        with get_session() as s:
            p = DBProduct(
                name="__PROBE__",
                product_type="crops",
                sku=f"PROBE-{int(__import__('time').time())}",
                unit_label="kg",
                is_active=True,
                default_cost_rate=1,
                default_sale_rate=1,
            )
            s.add(p)
            s.flush()
            return int(p.id)
        
    def get_or_create_party_id(self, party_type: str, display_name: str, phone: str = "") -> int:
        party_type = (party_type or "").strip().lower()
        name = (display_name or "").strip()
        phone = (phone or "").strip()

        if not name:
            raise ValueError("Source name is required.")

        with get_session() as s:
            try:
                q = (
                    s.query(Party)
                    .filter(Party.party_type == party_type)
                    .filter(Party.is_active == True)  # noqa: E712
                )

                if phone:
                    row = q.filter(Party.phone == phone).first()
                    if row:
                        if (row.display_name or "").strip() != name:
                            row.display_name = name
                            s.flush()
                        s.commit()
                        return int(row.id)

                row = q.filter(Party.display_name == name).first()
                if row:
                    if phone and not (row.phone or "").strip():
                        row.phone = phone
                        s.flush()
                    s.commit()
                    return int(row.id)

                row = Party(
                    party_type=party_type,
                    display_name=name,
                    phone=phone or None,
                    address="",
                    is_active=True,
                )
                s.add(row)
                s.flush()
                new_id = int(row.id)
                s.commit()
                return new_id

            except Exception:
                try: s.rollback()
                except Exception: pass
                raise

    def list_party_advances_db(self, party_id: int, session_user_id: int, limit: int = 500):
        party_id = int(party_id or 0)
        if party_id <= 0:
            return []

        uid = int(session_user_id or 0)
        if uid <= 0:
            raise ValueError("session_user_id is required (login session).")

        limit = max(1, min(int(limit or 500), 5000))

        with get_session() as s:
            rows = s.connection().exec_driver_sql(
                """
                SELECT id, party_id, direction, advance_type, product_id,
                    amount, qty_bags, total_weight_kg, unit_rate,
                    notes, status, created_by_user_id, created_at
                FROM advances
                WHERE party_id = %s
                AND created_by_user_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (party_id, uid, int(limit))
            ).fetchall()

        cols = [
            "id","party_id","direction","advance_type","product_id",
            "amount","qty_bags","total_weight_kg","unit_rate",
            "notes","status","created_by_user_id","created_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def list_products_by_advance_type_db(self, advance_type: str):
        t = (advance_type or "").strip().lower()
        if t not in ("crops", "fertilizer"):
            return []

        with get_session() as s:
            rows = s.connection().exec_driver_sql(
                """
                SELECT id, name
                FROM products
                WHERE product_type = %s AND COALESCE(is_active, 1) = 1
                ORDER BY name ASC
                """,
                (t,)
            ).fetchall()

        return [{"id": int(r[0]), "name": r[1]} for r in rows]

    def list_products_with_stock_db(self, q: str = "", category: str = "All", product_type: str = None, include_inactive: bool = False):
        from database.services.product_service import list_products_with_stock_db
        return list_products_with_stock_db(q=q, category=category, product_type=product_type, include_inactive=include_inactive)

    def create_product_db(self, payload: dict) -> int:
        from database.services.product_service import create_product_db, sku_exists_db
        sku = (payload.get("sku") or "").strip()
        if not sku:
            pt = (payload.get("product_type") or "").strip().lower()
            sku = self.make_intake_sku("fertilizer" if pt == "fertilizer" else "crops", payload.get("name") or "")
            base = sku
            i = 1
            while sku_exists_db(sku):
                sku = f"{base}-{i:03d}"
                i += 1
            payload["sku"] = sku
        else:
            if sku_exists_db(sku):
                raise ValueError("SKU already exists.")
        return create_product_db(payload)

    def update_product_db(self, product_id: int, payload: dict) -> None:
        from database.services.product_service import update_product_db, sku_exists_db
        sku = (payload.get("sku") or "").strip()
        if sku and sku_exists_db(sku, exclude_id=int(product_id)):
            raise ValueError("SKU already exists.")
        update_product_db(product_id, payload)

    def delete_product_db(self, product_id: int) -> None:
        from database.services.product_service import deactivate_product_db
        deactivate_product_db(product_id)

    def list_products_db(self, q: str = "", product_type=None) -> list[dict]:
        q = (q or "").strip().lower()
        pt = (product_type or "").strip().lower()
        if pt in ("all",):
            pt = ""

        with get_session() as s:
            where = "WHERE COALESCE(p.is_active, 1) = 1"
            args = []

            if pt in ("crops", "fertilizer"):
                where += " AND p.product_type = %s"
                args.append(pt)

            if q:
                where += " AND (LOWER(p.name) LIKE %s OR LOWER(p.sku) LIKE %s)"
                args.extend([f"%{q}%", f"%{q}%"])

            rows = s.connection().exec_driver_sql(
                f"""
                -- IN/OUT movement types here must match STOCK_IN_TYPES/
                -- STOCK_OUT_TYPES in database/models/stock_movement.py,
                -- the canonical, shared definition - this is raw SQL text,
                -- so it can't import those Python tuples directly, but
                -- must be kept in sync with them by hand.
                SELECT
                    p.id, p.name, p.product_type, p.category_id, p.sku, p.unit_label,
                    COALESCE(p.is_active, 1) AS is_active,
                    COALESCE(p.default_cost_rate, 0) AS default_cost_rate,
                    COALESCE(p.default_sale_rate, 0) AS default_sale_rate,
                    COALESCE(SUM(CASE WHEN sm.movement_type IN ('purchase_in','return_in','adjustment_in') THEN COALESCE(sm.qty_bags,0) WHEN sm.movement_type IN ('sale_out','adjustment_out','advance_out') THEN -COALESCE(sm.qty_bags,0) ELSE 0 END), 0) AS net_bags,
                    COALESCE(SUM(CASE WHEN sm.movement_type IN ('purchase_in','return_in','adjustment_in') THEN COALESCE(sm.total_weight_kg,0) WHEN sm.movement_type IN ('sale_out','adjustment_out','advance_out') THEN -COALESCE(sm.total_weight_kg,0) ELSE 0 END), 0) AS net_kg
                FROM products p
                LEFT JOIN stock_movements sm ON sm.product_id = p.id
                {where}
                GROUP BY p.id, p.name, p.product_type, p.category_id, p.sku, p.unit_label, p.is_active, p.default_cost_rate, p.default_sale_rate
                ORDER BY p.id DESC
                """,
                tuple(args)
            ).fetchall()

        cols = ["id","name","product_type","category_id","sku","unit_label","is_active","default_cost_rate","default_sale_rate","net_bags","net_kg"]
        out = [dict(zip(cols, r)) for r in rows]

        for d in out:
            d["stock_bags"] = d["net_bags"]
            d["stock_kg"] = d["net_kg"]
        return out
    
    def require_user_id(self) -> int:
        from shared.auth.session import require_user_id as _require
        uid = int(_require() or 0)
        if uid <= 0:
            raise ValueError("Session expired / no user logged in. Please login again.")
        return uid

    def bootstrap_session_from_db(self) -> None:
        uid = int((self.session_user or {}).get("id") or 0)
        if uid > 0:
            return

        try:
            with get_session() as s:
                row = s.connection().exec_driver_sql(
                    "SELECT id, username, COALESCE(role,'cashier') FROM users ORDER BY id ASC LIMIT 1"
                ).fetchone()

            if not row:
                raise ValueError("No users in DB. Create a user before using cashier actions.")

            self.session_user["id"] = int(row[0])
            self.session_user["name"] = str(row[1] or "").strip() or "User"
            self.session_user["role"] = str(row[2] or "cashier").strip().lower()
        except Exception as e:
            raise ValueError(f"Cannot bootstrap session from DB: {e}")
        
    def get_or_lazy_create_walkin(self, name: str, phone: str = "") -> int:
        """
        Looks up an existing casual walk-in by PHONE ONLY. If they do not
        exist (or no phone was given), a new row is created using the
        'walkin' party_type enum and committed to the database.

        Deliberately never matches on name alone. Name is not a reliable
        identity key - unrelated people commonly share the same name (and
        a blank-name entry used to default to the literal string
        "Walk-in Customer" for everyone), so matching on it silently
        merged a brand-new person's sale/payment into an unrelated,
        older party's history. The activity wasn't lost, it just landed
        under the wrong name and looked like it never registered. Phone
        is the only signal here worth trusting for "same person, reuse
        the record" - everything else always gets its own new row. A
        cashier who recognizes a returning no-phone walk-in should use
        the Walk-ins search screen to find and reuse that exact person
        instead of relying on this method to guess.
        """
        from database.session import get_session
        from database.models.party import Party

        name = name.strip() if name else "Unknown Walk-in"
        phone = phone.strip() if phone else ""

        with get_session() as s:
            row = None
            if phone:
                row = (
                    s.query(Party)
                    .filter(Party.party_type == "walkin", Party.phone == phone)
                    .first()
                )

            if row:
                return int(row.id)

            from database.services.walkin_service import WalkinService
            assigned_id = WalkinService.create(name, phone, source="till", session=s)["id"]
            s.commit()
            
            # Synchronize walkins state matrix cache automatically
            self._load_walkins_from_db()
            return assigned_id
            
state = AppState()
events = AppEvents()