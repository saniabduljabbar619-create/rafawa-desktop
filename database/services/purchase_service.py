#database/services/purchase_service.py
from datetime import datetime
from typing import Dict, List

from sqlalchemy import func

from database.session import get_session
from database.services.audit_service import AuditService
from shared.auth.permissions import require_admin
from database.services.walkin_account import is_shared_walkin
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.stock_movement import StockMovement
from database.models.party import Party
from database.models.payment import Payment
from database.models.purchase_return import PurchaseReturn


class PurchaseService:
    """
    Canonical purchase/intake service.

    Responsibilities:
        - validate purchase payload
        - upsert products
        - create purchase header
        - create purchase items
        - create stock movements
        - compute totals
    """

    # -------------------------------------------------
    # PUBLIC ENTRY POINT
    # -------------------------------------------------

    @staticmethod
    def record_intake(payload: dict) -> dict:

        p = dict(payload or {})

        product_name = str(p.get("product_name") or "").strip()
        sku = str(p.get("sku") or "").strip()

        intake_type = str(p.get("intake_type") or "").lower()

        # auto-detect if UI forgot
        if intake_type not in ("crops", "fertilizer"):
            if float(p.get("total_weight_kg") or 0) == 0:
                intake_type = "fertilizer"
            else:
                intake_type = "crops"

        qty_bags = int(p.get("qty_bags") or 0)
        total_weight_kg = float(p.get("total_weight_kg") or 0)
        unit_cost = float(p.get("unit_cost") or 0)

        supplier_party_id = int(p.get("supplier_party_id") or 0)
        created_by_user_id = int(p.get("created_by_user_id") or 0)

        if intake_type == "fertilizer":
            product_type = "fertilizer"
            total_weight_kg = 0
        else:
            product_type = "crops"

        item = {
            "sku": sku,
            "product_name": product_name,
            "product_type": product_type,
            "qty_bags": qty_bags,
            "total_weight_kg": total_weight_kg,
            "unit_cost": unit_cost,
        }

        purchase_payload = {
            "supplier_party_id": supplier_party_id,
            "created_by_user_id": created_by_user_id,
            "items": [item],
            "note": p.get("note", ""),
            "auto_flag_supplier": p.get("auto_flag_supplier", True),
        }

        result = PurchaseService.record(purchase_payload)

        # Advances are applied only via the explicit advance-choice prompt at
        # payment time - never automatically at creation.
        result["advance_used"] = 0.0
        result["remaining_payment"] = result["total_amount"]
        result["allocations"] = []

        return result


    @staticmethod
    def record(payload: Dict) -> Dict:
        """
        Record a multi-item purchase.

        payload["auto_flag_supplier"] (default True): whether recording
        this purchase should automatically flag the source party as a
        supplier. True is correct for genuine supplier intake (a real
        purchase from this party IS proof they're a supplier - see the
        comment on _ensure_role's call below). False is for a caller
        that explicitly chose an EXISTING CUSTOMER as the source (e.g.
        the customer profile's own "New Activity") - a customer being
        the source of one purchase should not silently, automatically
        make them show up in the Suppliers list too; converting them
        into a genuine supplier should be its own explicit action, not
        a side effect of recording one transaction.
        """

        PurchaseService._validate_payload(payload)

        items = payload["items"]
        supplier_party_id = payload["supplier_party_id"]
        created_by_user_id = payload["created_by_user_id"]
        note = payload.get("note", "")
        auto_flag_supplier = payload.get("auto_flag_supplier", True)

        with get_session() as s:

            # ----------------------------------------
            # Create purchase header
            # ----------------------------------------

            walkin_name = (payload.get("walkin_name") or "").strip()

            is_walkin = is_shared_walkin(supplier_party_id, s)
            if is_walkin:
                walkin_name = walkin_name or "Unknown"
            else:
                walkin_name = None

            # A real purchase FROM this party is proof they ARE a supplier,
            # whoever they were before - a longtime customer bought fertilizer
            # from us once, and this is the moment that becomes true too.
            # the walk-in id is the shared anonymous bucket, not a real counterparty.
            if not is_walkin and auto_flag_supplier:
                PurchaseService._ensure_role(supplier_party_id, "is_supplier")

            purchase = Purchase(
                supplier_party_id=supplier_party_id,
                walkin_name=walkin_name,
                status="completed",
                notes=note,
                subtotal=0,
                discount=0,
                total_amount=0,
                created_by_user_id=created_by_user_id,
                completed_at=datetime.now(),
                # Quick Purchase stamps these; a normal intake passes neither.
                is_quick=1 if payload.get("is_quick") else 0,
                linked_sale_id=payload.get("linked_sale_id"),
            )

            s.add(purchase)
            s.flush()

            subtotal = 0
            created_items = []

            # ----------------------------------------
            # Process items
            # ----------------------------------------

            for row in items:

                row = PurchaseService._normalize_item(row)

                product = PurchaseService._upsert_product(s, row)

                line_total = PurchaseService._compute_line_total(row)

                item = PurchaseItem(
                    purchase_id=purchase.id,
                    product_id=product.id,
                    product_type_snapshot=row["product_type"],
                    qty_bags=row["qty_bags"],
                    total_weight_kg=row["total_weight_kg"],
                    unit_cost_rate=row["unit_cost"],
                    line_total=line_total,
                    notes=row.get("note")
                )

                s.add(item)
                s.flush()

                PurchaseService._record_stock_movement(
                    s,
                    product.id,
                    item.id,
                    supplier_party_id,
                    created_by_user_id,
                    row
                )

                subtotal += line_total
                created_items.append(item.id)

            purchase.subtotal = subtotal
            purchase.total_amount = subtotal

            s.commit()

            return {
                "purchase_id": purchase.id,
                "purchase_item_ids": created_items,
                "total_amount": subtotal
            }

    # -------------------------------------------------
    # VALIDATION
    # -------------------------------------------------

    @staticmethod
    def _validate_payload(payload: Dict):

        if "supplier_party_id" not in payload:
            raise ValueError("supplier_party_id required")

        try:
            supplier_ok = int(payload.get("supplier_party_id") or 0) > 0
        except (TypeError, ValueError):
            supplier_ok = False
        if not supplier_ok:
            raise ValueError("supplier_party_id must be a valid party id")

        if "created_by_user_id" not in payload:
            raise ValueError("created_by_user_id required")

        items = payload.get("items")

        if not items:
            raise ValueError("purchase must contain at least one item")

    
    # -------------------------------------------------
    # ITEM NORMALIZATION
    # -------------------------------------------------

    @staticmethod
    def _normalize_item(row: Dict) -> Dict:

        # ----------------------------------------
        # Normalize product type
        # ----------------------------------------

        pt = str(row.get("product_type") or "").strip().lower()

        if pt in ("crop", "crops"):
            product_type = "crops"

        elif pt in ("fert", "fertilizer", "fertilizers"):
            product_type = "fertilizer"

        else:
            raise ValueError(f"Invalid product_type: {pt}")

        # ----------------------------------------
        # Validate bags
        # ----------------------------------------

        qty_bags = int(row.get("qty_bags") or row.get("bags") or 0)

        if qty_bags <= 0:
            raise ValueError("qty_bags must be > 0")

        # ----------------------------------------
        # Validate unit cost
        # ----------------------------------------

        unit_cost = float(row.get("unit_cost") or 0)

        if unit_cost < 0:
            raise ValueError("unit_cost cannot be negative")

        # ----------------------------------------
        # Handle weight rules
        # ----------------------------------------

        if product_type == "fertilizer":

            # Rafawa rule: fertilizer is bags-only
            total_weight_kg = 0.0

        else:

            total_weight_kg = float(row.get("total_weight_kg") or 0)

            if total_weight_kg <= 0:
                raise ValueError("total_weight_kg must be > 0 for crops")

        # ----------------------------------------
        # Write normalized values back
        # ----------------------------------------

        row["product_type"] = product_type
        row["qty_bags"] = qty_bags
        row["total_weight_kg"] = total_weight_kg
        row["unit_cost"] = unit_cost

        return row
    # -------------------------------------------------
    # PRODUCT UPSERT
    # -------------------------------------------------

    @staticmethod
    def _upsert_product(session, row: Dict):

        sku = row["sku"]
        name = row["product_name"]

        if sku:
            product = session.query(Product).filter(Product.sku == sku).first()
        else:
            # A blank SKU must never match "the first product with a blank
            # SKU" - that silently overwrote an unrelated product's name,
            # type and cost. Fall back to an exact name + type match among
            # SKU-less products instead.
            product = (
                session.query(Product)
                .filter(func.lower(Product.name) == name.lower())
                .filter(Product.product_type == row["product_type"])
                .filter((Product.sku.is_(None)) | (Product.sku == ""))
                .first()
            )

        if not product:

            product = Product(
                name=name,
                sku=sku or None,
                product_type=row["product_type"],
                unit_label="bag" if row["product_type"] == "fertilizer" else "kg",
                default_cost_rate=row["unit_cost"],
                is_active=True
            )

            session.add(product)
            session.flush()

        else:

            product.name = name
            product.product_type = row["product_type"]
            product.default_cost_rate = row["unit_cost"]
            product.is_active = True

            session.flush()

        return product

    # -------------------------------------------------
    # LINE TOTAL
    # -------------------------------------------------

    @staticmethod
    def _compute_line_total(row: Dict) -> float:

        if row["product_type"] == "fertilizer":

            return float(row["qty_bags"]) * float(row["unit_cost"])

        return float(row["total_weight_kg"]) * float(row["unit_cost"])

    # -------------------------------------------------
    # STOCK MOVEMENT
    # -------------------------------------------------

    @staticmethod
    def _record_stock_movement(
        session,
        product_id,
        purchase_item_id,
        supplier_party_id,
        created_by_user_id,
        row
    ):

        movement = StockMovement(
            product_id=product_id,
            movement_type="purchase_in",
            qty_bags=row["qty_bags"],
            total_weight_kg=row["total_weight_kg"],
            unit_rate=row["unit_cost"],
            reference_type="purchase_item",
            reference_id=purchase_item_id,
            party_id=supplier_party_id,
            created_by_user_id=created_by_user_id
        )

        session.add(movement)
        session.flush()
    @staticmethod
    def _ensure_role(party_id: int, flag_name: str) -> None:
        """Idempotently flips one capability flag true on a party. See
        SaleService._ensure_role - same pattern, mirrored for the buy side,
        including the walk-in skip (a walk-in supplier must never leak into
        the real Suppliers list)."""
        with get_session() as s:
            p = s.get(Party, int(party_id))
            if p is None or str(p.party_type or "") == "walkin":
                return
            if not getattr(p, flag_name, True):
                setattr(p, flag_name, True)

    # -------------------------------------------------
    # VOID PURCHASE (Case B: the transaction itself, before any
    # payment exists against it - mistake correction, keeps history)
    # -------------------------------------------------
    @staticmethod
    @require_admin
    def void_purchase(purchase_id: int, voided_by_user_id: int, reason: str = "") -> Dict:
        """
        Voids a purchase transaction that was entered wrong from the start
        (wrong product, wrong quantity, wrong supplier entirely) - not a
        payment problem, the transaction itself. Refuses the moment ANY
        payment exists against it, even a voided one's original existence
        - once real money has been recorded here, correcting it is
        PaymentService.void_payment's job on that payment first, not this.

        Never deletes or edits the original PurchaseItem/StockMovement
        rows in place: reverses the stock this purchase added by
        appending an offsetting movement for each line, the same
        append-only convention already used for void_payment and
        void_advance. The purchase itself then gets status="voided" -
        stays visible in history forever, but drops out of every
        balance and stock calculation from this point on.
        """
        purchase_id = int(purchase_id or 0)
        voided_by_user_id = int(voided_by_user_id or 0)
        if purchase_id <= 0:
            raise ValueError("purchase_id is required.")
        if voided_by_user_id <= 0:
            raise ValueError("voided_by_user_id is required.")

        with get_session() as s:
            purchase = s.get(Purchase, purchase_id)
            if purchase is None:
                raise ValueError("Purchase not found.")
            if purchase.status == "voided":
                return {"ok": True, "purchase_id": purchase_id, "already_voided": True}

            has_payment = (
                s.query(Payment)
                .filter(Payment.entity_type == "purchase", Payment.entity_id == purchase_id)
                .first()
            )
            if has_payment is not None:
                raise ValueError(
                    "Cannot void this purchase - a payment already exists "
                    "against it (even if that payment has itself been "
                    "voided). Void the specific payment(s) first if they're "
                    "wrong; this action is only for a transaction that was "
                    "never paid against at all."
                )

            has_return = (
                s.query(PurchaseReturn.id)
                .filter(PurchaseReturn.purchase_id == purchase_id)
                .first()
            )
            if has_return is not None:
                raise ValueError(
                    "Cannot void this purchase - goods have already been "
                    "returned against it. Voiding would take the returned "
                    "stock out a second time."
                )

            items = s.query(PurchaseItem).filter(PurchaseItem.purchase_id == purchase_id).all()
            reversed_movements = 0
            for item in items:
                movements = (
                    s.query(StockMovement)
                    .filter(StockMovement.reference_type == "purchase_item",
                           StockMovement.reference_id == item.id)
                    .all()
                )
                for mv in movements:
                    # purchase_in -> adjustment_out (stock leaving again,
                    # correcting the mistake); anything else already
                    # represents an outflow, so its reversal is an inflow.
                    reversal_type = "adjustment_out" if mv.movement_type == "purchase_in" else "adjustment_in"
                    s.add(StockMovement(
                        product_id=mv.product_id,
                        movement_type=reversal_type,
                        qty_bags=mv.qty_bags,
                        total_weight_kg=mv.total_weight_kg,
                        unit_rate=mv.unit_rate,
                        reference_type="purchase_item",
                        reference_id=item.id,
                        party_id=mv.party_id,
                        created_by_user_id=voided_by_user_id,
                    ))
                    reversed_movements += 1

            purchase.status = "voided"
            if reason:
                purchase.notes = ((purchase.notes or "") + f" | [Voided: {reason}]").strip()
            AuditService.record(
                "purchase.void", "purchase", purchase_id,
                f"Voided purchase of {float(purchase.total_amount or 0):,.2f}",
                {"reason": reason, "total": float(purchase.total_amount or 0),
                 "supplier_party_id": purchase.supplier_party_id,
                 "stock_movements_reversed": reversed_movements}, session=s)

        return {"ok": True, "purchase_id": purchase_id, "stock_movements_reversed": reversed_movements}

    @staticmethod
    def list_voidable_purchases(party_id: int, limit: int = 200) -> List[Dict]:
        """
        Purchases for this party that void_purchase would actually accept -
        no payment against them at all, and not already voided. What the
        Revert Mistakes tab's "Purchase Transaction" option lists.
        """
        party_id = int(party_id or 0)
        with get_session() as s:
            purchases = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(Purchase.status != "voided")
                .order_by(Purchase.created_at.desc())
                .limit(limit)
                .all()
            )
            paid_ids = {
                int(eid) for (eid,) in
                s.query(Payment.entity_id)
                .filter(Payment.entity_type == "purchase",
                       Payment.entity_id.in_([p.id for p in purchases]))
                .distinct()
                .all()
            } if purchases else set()

        return [
            {
                "id": int(p.id),
                "reference": p.purchase_no or f"PUR-{p.id}",
                "amount": float(p.total_amount or 0),
                "notes": p.notes or "",
                "created_at": p.created_at,
            }
            for p in purchases if int(p.id) not in paid_ids
        ]

    @staticmethod
    def list_activity_with_shortages(party_id: int, limit: int = 300) -> List[Dict]:
        """
        Every purchase for this party, regardless of payment status -
        unlike list_voidable_purchases, this deliberately INCLUDES
        fully-paid ones. See SaleService.list_activity_with_shortages for
        the full reasoning - the mirror method for the purchase side.
        """
        party_id = int(party_id or 0)
        with get_session() as s:
            purchases = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(Purchase.status != "voided")
                .order_by(Purchase.created_at.desc())
                .limit(limit)
                .all()
            )
            pur_ids = [p.id for p in purchases]
            paid_by_id: Dict[int, float] = {}
            if pur_ids:
                for eid, amt in (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "purchase", Payment.entity_id.in_(pur_ids))
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                ):
                    paid_by_id[int(eid)] = float(amt or 0)

            # ---- product summary, same pattern as DrawerService.party_transactions ----
            products = {int(pr.id): (pr.name or f"Product#{pr.id}", pr.product_type or "")
                       for pr in s.query(Product).all()}
            pur_lines: Dict[int, List[int]] = {}
            if pur_ids:
                for pi in s.query(PurchaseItem).filter(PurchaseItem.purchase_id.in_(pur_ids)).all():
                    pur_lines.setdefault(int(pi.purchase_id), []).append(int(pi.product_id))

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

        from database.services.cash_shortage_service import CashShortageService
        open_shortages = CashShortageService.list_open_shortages(party_id, reference_type="purchase")
        shortage_by_ref: Dict[int, Dict] = {int(sh["reference_id"]): sh for sh in open_shortages}

        return [
            {
                "id": int(p.id),
                "reference": p.purchase_no or f"PUR-{p.id}",
                "amount": float(p.total_amount or 0),
                "paid": paid_by_id.get(int(p.id), 0.0),
                "created_at": p.created_at,
                "open_shortage": shortage_by_ref.get(int(p.id)),
                "product_summary": _product_summary(pur_lines.get(int(p.id), [])),
            }
            for p in purchases
        ]