#database/services/intake_service.py
from __future__ import annotations

from typing import Optional, List, Dict

from database.session import get_session

from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.product import Product
from database.models.party import Party

from datetime import datetime
from sqlalchemy import desc, text

from database.models.product import Product as DBProduct
from database.models.stock_movement import StockMovement

# Verify that Party model has 'name' and 'phone' attributes
# If the actual column names differ (e.g., 'party_name', 'party_phone'), update the references below

def record_intake_db(p: dict) -> dict:
    """
    Canonical DB-backed intake writer.
    Works for ALL intake dialogs.

    Expected canonical payload keys (minimum):
      - intake_type: "crops" | "fertilizer"
      - product_name: str
      - sku: str
      - unit: "kg" | "bag" (we normalize)
      - qty: float              # crops: total kg (or compat)
      - total_weight_kg: float  # crops REQUIRED; fertilizer forced to 0
      - qty_bags: int           # REQUIRED (bags count)
      - unit_cost: float        # crops: per kg, fertilizer: per bag
      - supplier_party_id: int
      - created_by_user_id: int  # REQUIRED (session user id)

    Rules (LOCKED):
      - Fertilizer is bags-only: total_weight_kg = 0, qty = qty_bags, unit="bag"
      - Crops is kg-based: total_weight_kg > 0, unit="kg"
      - Never infer qty_bags. Never default bags to 1.
      - Always write stock_movements using (qty_bags, total_weight_kg).
      - Must have valid created_by_user_id (FK-safe).
    """
    import logging
    import traceback

    from database.session import get_session
    from database.models.purchase import Purchase
    from database.models.purchase_item import PurchaseItem

    _logger = logging.getLogger("rafawa.record_intake")

    def _log(msg: str) -> None:
        # Errors surface on stderr even with no logging configured.
        (_logger.error if msg.startswith("[ERR]") else _logger.info)(msg)

    p = dict(p or {})
    _log(f"[HIT] record_intake_db payload={p}")

    # -------------------------
    # Read + validate payload
    # -------------------------
    intake_type = str(p.get("intake_type") or "").strip().lower()
    if intake_type not in ("crops", "fertilizer"):
        raise ValueError("intake_type must be 'crops' or 'fertilizer'")

    product_name = str(p.get("product_name") or "").strip()
    if not product_name:
        raise ValueError("product_name is required")

    sku = str(p.get("sku") or "").strip()
    if not sku:
        raise ValueError("sku is required")

    unit = str(p.get("unit") or "kg").strip().lower()
    if unit not in ("kg", "bag"):
        raise ValueError("unit must be 'kg' or 'bag'")

    qty_bags = int(p.get("qty_bags") or p.get("bags") or 0)
    if qty_bags <= 0:
        raise ValueError("qty_bags (bags) must be > 0")

    unit_cost = float(p.get("unit_cost") or 0.0)
    if unit_cost < 0:
        raise ValueError("unit_cost cannot be negative")

    unit_sale_price = p.get("unit_sale_price", None)
    if unit_sale_price is not None:
        unit_sale_price = float(unit_sale_price)

    note = str(p.get("note") or "")

    supplier_party_id = int(p.get("supplier_party_id") or 0)
    if supplier_party_id <= 0:
        raise ValueError("supplier_party_id is required")

    created_by_user_id = int(p.get("created_by_user_id") or 0)
    if created_by_user_id <= 0:
        raise ValueError("created_by_user_id is required (login session).")

    # -------------------------
    # Canonical normalization
    # -------------------------
    product_type = "crops" if intake_type == "crops" else "fertilizer"

    if product_type == "fertilizer":
        # ✅ Phase 1.1: bags-only
        unit = "bag"
        total_weight_kg = 0.0
        qty = float(qty_bags)
        line_total = float(qty_bags) * float(unit_cost)  # per bag
    else:
        # ✅ crops: kg-based
        unit = "kg"
        qty = float(p.get("qty") or 0.0)
        total_weight_kg = p.get("total_weight_kg")
        if total_weight_kg is None:
            total_weight_kg = qty  # compat fallback only if qty is kg
        total_weight_kg = float(total_weight_kg)

        if total_weight_kg <= 0:
            raise ValueError("total_weight_kg must be > 0 for crops")
        if qty <= 0:
            # keep qty consistent for any legacy math; use total_weight_kg
            qty = float(total_weight_kg)

        line_total = float(total_weight_kg) * float(unit_cost)  # per kg

    # -------------------------
    # Write to DB
    # -------------------------
    with get_session() as s:
        try:
            dbname = s.execute(text("SELECT DATABASE()")).scalar()
            _log(f"[DB] connected database={dbname}")

            # 1) UPSERT PRODUCT (by SKU)
            prod = s.query(DBProduct).filter(DBProduct.sku == sku).first()
            mode = "updated" if prod else "created"

            if not prod:
                prod = DBProduct(
                    name=product_name,
                    sku=sku,
                    product_type=product_type,
                    unit_label=unit,
                    default_cost_rate=unit_cost,
                    default_sale_rate=unit_sale_price,
                    is_active=True,
                )
                s.add(prod)
                s.flush()
                _log(f"[DB] product created id={prod.id}")
            else:
                prod.name = product_name
                prod.product_type = product_type
                prod.unit_label = unit
                prod.default_cost_rate = unit_cost
                if unit_sale_price is not None:
                    prod.default_sale_rate = unit_sale_price
                prod.is_active = True
                s.flush()
                _log(f"[DB] product updated id={prod.id}")

            product_id = int(prod.id)

            # 2) PURCHASE (header)
            purchase = Purchase(
                supplier_party_id=supplier_party_id,
                status="completed",
                notes=note,
                subtotal=line_total,
                discount=0,
                total_amount=line_total,
                created_by_user_id=created_by_user_id,
                completed_at=datetime.now(),
            )
            s.add(purchase)
            s.flush()
            _log(f"[DB] purchase created id={purchase.id}")

            # 3) PURCHASE ITEM (line)
            item = PurchaseItem(
                purchase_id=int(purchase.id),
                product_id=product_id,
                product_type_snapshot=product_type,
                qty_bags=int(qty_bags),
                total_weight_kg=(0.0 if product_type == "fertilizer" else float(total_weight_kg)),
                unit_cost_rate=float(unit_cost),
                line_total=float(line_total),
                notes=note,
            )
            s.add(item)
            s.flush()
            _log(f"[DB] purchase_item created id={item.id}")

            # 4) STOCK MOVEMENT
            movement = StockMovement(
                product_id=product_id,
                movement_type="purchase_in",
                qty_bags=int(qty_bags),
                total_weight_kg=(0.0 if product_type == "fertilizer" else float(total_weight_kg)),
                unit_rate=float(unit_cost),
                reference_type="purchase_item",
                reference_id=int(item.id),
                party_id=int(supplier_party_id),
                created_by_user_id=created_by_user_id,
            )
            s.add(movement)
            s.flush()
            _log(f"[DB] stock_movement created id={movement.id}")

            s.commit()
            _log("[DB] COMMIT OK")

            return {
                "mode": mode,
                "purchase_id": int(purchase.id),
                "purchase_item_id": int(item.id),
                "stock_movement_id": int(movement.id),
                "product_id": int(product_id),
                "reference": f"PUR-{purchase.id}",
                "qty_bags": int(qty_bags),
                "total_weight_kg": float(0.0 if product_type == "fertilizer" else float(total_weight_kg)),
                "total_amount": float(line_total),
                "created_by_user_id": int(created_by_user_id),
                "supplier_party_id": int(supplier_party_id),
            }

        except Exception as e:
            try:
                s.rollback()
            except Exception:
                pass
            _log("[DB] ROLLBACK")
            _log("[ERR] " + repr(e))
            _log(traceback.format_exc())
            raise


def list_customer_intakes_db(customer_id: int, q: str = "", session_user_id: int = 0) -> list[dict]:
    """
    Returns intake rows for a given customer_id (supplier_party_id in Purchase),
    scoped to a cashier session (created_by_user_id).

    Shape matches CustomerIntakesTab:
      dt, ref, product, sku, qty_added, unit_price, source, processed_by
    """
    cid = int(customer_id or 0)
    uid = int(session_user_id or 0)
    q = (q or "").strip().lower()

    if cid <= 0:
        return []
    if uid <= 0:
        raise ValueError("session_user_id is required")

    with get_session() as s:
        rows = (
            s.query(
                Purchase.created_at,
                Purchase.id.label("purchase_id"),
                Purchase.supplier_party_id,
                Purchase.created_by_user_id,
                Purchase.notes.label("notes"),

                PurchaseItem.qty_bags,
                PurchaseItem.total_weight_kg,
                PurchaseItem.unit_cost_rate,
                PurchaseItem.product_type_snapshot,          # ✅ needed for qty display

                DBProduct.name.label("product_name"),
                DBProduct.sku.label("sku"),

                Party.display_name.label("party_name"),
                Party.phone.label("party_phone"),
            )
            .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
            .join(DBProduct, DBProduct.id == PurchaseItem.product_id)
            .join(Party, Party.id == Purchase.supplier_party_id)
            .filter(Purchase.status == "completed")
            .filter(Purchase.supplier_party_id == cid)
            .filter(Purchase.created_by_user_id == uid)     # ✅ Phase-5 isolation
            .order_by(desc(Purchase.id))
            .all()
        )

    out: list[dict] = []
    for r in rows:
        dt = (r.created_at.isoformat(sep="T", timespec="seconds") if r.created_at else "")
        ref = f"PUR-{int(r.purchase_id)}"
        product = str(r.product_name or "")
        sku = str(r.sku or "")

        # ✅ qty display must respect fertilizer bags-only rule
        ptype = (str(getattr(r, "product_type_snapshot", "") or "")).strip().lower()
        if ptype in ("fertilizer", "fertilizers"):
            qty_added = float(r.qty_bags or 0)
        else:
            qty_added = float(r.total_weight_kg or 0.0)

        unit_price = float(r.unit_cost_rate or 0.0)

        meta = _parse_notes(getattr(r, "notes", "") or "")
        source = meta.get("source", "") or meta.get("source_name", "") or str(r.party_name or "")
        processed_by = meta.get("processed_by", "") or str(r.created_by_user_id or "")

        hay = " ".join([dt, ref, product, sku, source, processed_by]).lower()
        if q and q not in hay:
            continue

        out.append({
            "dt": dt,
            "ref": ref,
            "product": product,
            "sku": sku,
            "qty_added": qty_added,
            "unit_price": unit_price,
            "source": source,
            "processed_by": processed_by,
        })

    return out


def _parse_notes(notes: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not notes:
        return out
    # notes format: "k=v; k=v; ..."
    parts = [p.strip() for p in str(notes).split(";") if p.strip()]
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip()

    return out


def list_intakes_db(limit: int = 200, q: str = "", session_user_id: int = 0) -> List[Dict]:
    """
    Cashier Intake (Delivery) list.
    Must be session-scoped: only show rows created by the logged-in cashier.
    """
    limit = max(1, min(int(limit or 200), 2000))
    q = (q or "").strip().lower()
    uid = int(session_user_id or 0)
    if uid <= 0:
        raise ValueError("session_user_id is required")

    with get_session() as s:
        rows = (
            s.query(
                Purchase.id.label("purchase_id"),
                Purchase.created_at.label("dt"),
                Purchase.notes.label("notes"),
                Purchase.created_by_user_id.label("created_by_user_id"),

                Party.display_name.label("customer_name"),
                Party.phone.label("customer_phone"),

                Product.name.label("product_name"),
                Product.sku.label("sku"),

                PurchaseItem.qty_bags.label("qty_bags"),
                PurchaseItem.total_weight_kg.label("total_weight_kg"),
                PurchaseItem.unit_cost_rate.label("unit_price"),
                PurchaseItem.product_type_snapshot.label("ptype"),   # ✅ needed for fertilizer qty
            )
            .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
            .join(Product, Product.id == PurchaseItem.product_id)
            .join(Party, Party.id == Purchase.supplier_party_id)
            .filter(Purchase.status == "completed")
            .filter(Purchase.created_by_user_id == uid)            # ✅ THE FIX (scope)
            .order_by(Purchase.created_at.desc(), Purchase.id.desc())
            .limit(limit)
        )

        rows = rows.all()

    out: List[Dict] = []
    for r in rows:
        meta = _parse_notes(r.notes or "")
        cust = (r.customer_name or "Customer").strip()
        ph = (r.customer_phone or "").strip()
        if ph:
            cust = f"{cust} ({ph})"

        ptype = (str(getattr(r, "ptype", "") or "")).strip().lower()

        # ✅ fertilizer shows bags, crops shows kg
        if ptype in ("fertilizer", "fertilizers"):
            qty_val = float(r.qty_bags or 0)
        else:
            qty_val = float(r.total_weight_kg or 0.0)

        processed_by = meta.get("processed_by", "")
        if not processed_by:
            processed_by = str(r.created_by_user_id or "")

        ref = str(r.purchase_id or "")
        dt = str(r.dt or "")

        hay = " ".join([
            dt, ref, cust, str(r.product_name or ""), str(r.sku or ""),
            meta.get("source", ""), processed_by
        ]).lower()

        if q and q not in hay:
            continue

        out.append({
            "dt": dt,
            "ref": ref,
            "customer": cust,
            "product_name": str(r.product_name or ""),
            "sku": str(r.sku or ""),
            "qty_kg": qty_val,  # keep key name (UI expects it)
            "unit_price": float(r.unit_price or 0.0),
            "source_name": meta.get("source", ""),
            "processed_by": processed_by,
        })

    return out

