# apps/product_management_app/features/receipts/activity_receipt_printer.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# Printing entry point (RAW -> Qt fallback)
from apps.product_management_app.features.receipts.receipt_printer import print_text_receipt

# DB session + models
from database.session import get_session


@dataclass(frozen=True)
class ShopMeta:
    shop_name: str = "Alh Zakari Rafawa Enterprises"
    address: str = "No 18 Kwanar Tsau, Gwarzo Kano State"
    phone: str = "07046329444"
    paper_width: int = 48  # 48 ~ 80mm, 32 ~ 58mm


def _fmt_dt(dt: Any) -> str:
    if not dt:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)


def _money(x: Any) -> str:
    try:
        return f"NGN {float(x or 0.0):,.2f}"
    except Exception:
        return "NGN 0.00"



def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s[:n] if len(s) > n else s


def _center(s: str, w: int) -> str:
    s = (s or "").strip()
    if len(s) >= w:
        return s[:w]
    pad = (w - len(s)) // 2
    return (" " * pad) + s


def _lr(left: str, right: str, w: int) -> str:
    left = (left or "")
    right = (right or "")
    space = w - len(left) - len(right)
    if space < 1:
        # clip left
        left = _clip(left, max(0, w - len(right) - 1))
        space = w - len(left) - len(right)
        if space < 1:
            space = 1
    return f"{left}{' ' * space}{right}"


def _extract_ledger_from_notes(notes: str) -> str:
    """
    Reads 'ledger=crops' or 'ledger=fertilizers' from notes if present.
    Safe fallback: crops.
    """
    s = (notes or "").lower()
    if "ledger=fertilizers" in s or "ledger=fertilizer" in s:
        return "fertilizers"
    if "ledger=crops" in s or "ledger=crop" in s:
        return "crops"
    return "crops"
def _qty_label_for_sale_item(it: Any) -> str:
    """
    Quantity label for SALE receipt lines.

    Policy:
    - Fertilizer: bags-only
    - Crops: bags + kg (when both exist)
    """
    ptype = (getattr(it, "product_type_snapshot", "") or "").strip().lower()
    bags = float(getattr(it, "qty_bags", 0) or 0)
    kg = float(
        getattr(it, "total_weight_kg", 0) or
        getattr(it, "qty_kg", 0) or
        0
    )

    if ptype in ("fertilizer", "fertilizers"):
        return f"{int(bags)} bag" + ("s" if bags != 1 else "")

    # crops
    if bags > 0 and kg > 0:
        return f"{int(bags)} bag, {kg:,.2f}kg"
    if bags > 0:
        return f"{int(bags)} bag" + ("s" if bags != 1 else "")
    return f"{kg:,.2f}kg"


class ActivityReceipt:
    """
    Single-file receipt engine driven by ActivityHistory rows.

    Supported activity_type:
      - "payment"  -> database.models.payment.Payment
      - "sale"     -> database.models.sale.Sale (+ sale items optional)
      - "intake"   -> database.models.purchase.Purchase (+ purchase items optional)
      - "advance"  -> intentionally blocked (per your rule)
    """

    def __init__(self, shop: ShopMeta):
        self.shop = shop

    # ---------------- Public API ----------------

    def print_from_activity(
        self,
        parent,
        *,
        activity_type: str,
        activity_id: int,
        cashier_name_override: str = "",
        printer_name: Optional[str] = None,
    ) -> None:
        t = (activity_type or "").strip().lower()
        rid = int(activity_id or 0)
        if rid <= 0:
            raise ValueError("activity_id must be > 0")

        if t == "advance":
            raise RuntimeError("Advance printing is disabled (policy: Advance = NO).")

        # Build receipt text (DB source of truth)
        text = self._build_receipt_text(
            activity_type=t,
            activity_id=rid,
            cashier_name_override=cashier_name_override,
        )

        # Print (RAW -> Qt fallback)
        print_text_receipt(
            parent,
            text,
            printer_name=printer_name,
            prefer_raw=True,
            paper_mm_width=80.0 if int(self.shop.paper_width) == 48 else 58.0,
        )

    # ---------------- Builders ----------------

    def _build_receipt_text(
        self,
        *,
        activity_type: str,
        activity_id: int,
        cashier_name_override: str = "",
    ) -> str:
        t = (activity_type or "").strip().lower()

        if t == "payment":
            return self._receipt_payment(activity_id, cashier_name_override=cashier_name_override)
        if t == "sale":
            return self._receipt_sale(activity_id, cashier_name_override=cashier_name_override)
        if t == "intake":
            return self._receipt_intake(activity_id, cashier_name_override=cashier_name_override)

        raise RuntimeError(f"Unsupported activity_type: {activity_type}")

    # ---------------- Common blocks ----------------

    def _header_block(self) -> str:
        w = int(self.shop.paper_width or 48)
        lines = [
            _center(self.shop.shop_name, w),
            _center(self.shop.address, w),
            _center(f"Phone: {self.shop.phone}", w),
            "",
            "",
        ]
        return "\n".join(lines)

    def _footer_block(self, receipt_id: int) -> str:
        w = int(self.shop.paper_width or 48)
        return "\n".join([
            "",
            _center("Thank you for your patronage!", w),
            _center(f"Receipt ID: {int(receipt_id)}", w),
            "",
        ])

    def _desc_header(self) -> str:
        w = int(self.shop.paper_width or 48)
        return "\n".join([
            _lr("Description", "Amount (₦)", w),
            "-" * w,
        ])


    def _qty_label_for_sale_item(it: Any) -> str:
        """
        Fertilizer: bags-only.
        Crops: show bags + kg when present.
        """
        ptype = (getattr(it, "product_type_snapshot", "") or "").strip().lower()
        bags = float(getattr(it, "qty_bags", 0) or 0)
        kg = float(getattr(it, "total_weight_kg", 0) or getattr(it, "qty_kg", 0) or 0)

        if ptype in ("fertilizer", "fertilizers"):
            # bags-only policy
            return f"{bags:.0f} bag" + ("s" if bags != 1 else "")
        # crops
        if bags > 0 and kg > 0:
            return f"{bags:.0f} bag, {kg:,.2f}kg"
        if bags > 0:
            return f"{bags:.0f} bag" + ("s" if bags != 1 else "")
        return f"{kg:,.2f}kg"


    # ---------------- Payment receipt ----------------

    def _receipt_payment(self, payment_id: int, *, cashier_name_override: str = "") -> str:
        # Models imported here to avoid import cycles
        from database.models.payment import Payment
        from database.models.party import Party

        w = int(self.shop.paper_width or 48)

        with get_session() as s:
            p = s.query(Payment).filter(Payment.id == int(payment_id)).first()
            if not p:
                raise RuntimeError(f"Payment not found: id={payment_id}")

            party = None
            try:
                party_id = int(getattr(p, "party_id", 0) or 0)
                if party_id > 0:
                    party = s.query(Party).filter(Party.id == party_id).first()
            except Exception:
                party = None

        # Values
        dt = _fmt_dt(getattr(p, "created_at", None))
        amount = float(getattr(p, "amount", 0.0) or 0.0)
        notes = (getattr(p, "notes", "") or "")
        ledger = _extract_ledger_from_notes(notes)
        ledger_label = "Fertilizers" if ledger == "fertilizers" else "Crops"

        cashier = (cashier_name_override or "").strip()
        if not cashier:
            cashier = "Cashier"

        # Ref
        et = (getattr(p, "entity_type", "") or "").strip().upper()
        entity_id = int(getattr(p, "entity_id", 0) or 0)
        invoice = f"{et}-{entity_id:05d}" if (et and entity_id > 0) else f"PAY-{payment_id:05d}"

        # Customer
        customer_name = (getattr(party, "display_name", "") or "").strip() if party else ""

        # Status
        direction = (getattr(p, "direction", "") or "").strip().lower()
        is_advance = (et.lower() == "advance") or (direction == "out")
        title = "ADVANCE RECEIPT" if is_advance else "PAYMENT RECEIPT"
        status = "Posted"

        # Build (matches your sample: compact but professional)
        out = []
        out.append(self._header_block())
        out.append(_center(title, w))
        out.append("")
        out.append(_lr("Invoice:", invoice, w))
        out.append(_lr("Date:", dt, w))
        out.append(_lr("Cashier:", cashier, w))
        out.append(_lr("Status:", status, w))
        if customer_name:
            out.append(_lr("Customer:", _clip(customer_name, w - 10), w))
        out.append(_lr("Ledger:", ledger_label, w))
        out.append("")
        out.append(self._desc_header())
        out.append(_lr("Payment Amount:", _money(amount), w))
        out.append("-" * w)
        out.append(self._footer_block(payment_id))
        return "\n".join(out).rstrip() + "\n"

    # ---------------- Sale receipt ----------------

    def _receipt_sale(self, sale_id: int, *, cashier_name_override: str = "") -> str:
        from database.models.sale import Sale
        from database.models.sale_item import SaleItem
        from database.models.party import Party
        from database.services.payment_service import PaymentService

        w = int(self.shop.paper_width or 48)

        with get_session() as s:
            sale = s.query(Sale).filter(Sale.id == int(sale_id)).first()
            if not sale:
                raise RuntimeError(f"Sale not found: id={sale_id}")

            party = None
            try:
                party_id = int(getattr(sale, "buyer_party_id", 0) or getattr(sale, "party_id", 0) or 0)
                if party_id > 0:
                    party = s.query(Party).filter(Party.id == party_id).first()
            except Exception:
                party = None

            items = (
                s.query(SaleItem)
                .filter(SaleItem.sale_id == int(sale_id))
                .order_by(SaleItem.id.asc())
                .all()
            )

        dt = _fmt_dt(getattr(sale, "created_at", None))
        total = float(getattr(sale, "total_amount", 0.0) or getattr(sale, "amount_total", 0.0) or 0.0)

        # ✅ DB truth: derive paid from payments linked to this sale
        paid = float(PaymentService.sum_paid("sale", int(sale_id), direction="in") or 0.0)
        due = max(0.0, float(total) - float(paid))

        status = "Paid" if due <= 0 else ("Half" if paid > 0 else "Unpaid")
        cashier = (cashier_name_override or "").strip() or "Cashier"
        invoice = f"SALE-{sale_id:05d}"

        customer_name = (getattr(party, "display_name", "") or "").strip() if party else ""

        # Determine ledger from items snapshot
        ledger = "crops"
        for it in items:
            ptype = (getattr(it, "product_type_snapshot", "") or "").strip().lower()
            if ptype in ("fertilizer", "fertilizers"):
                ledger = "fertilizers"
                break
        ledger_label = "Fertilizers" if ledger == "fertilizers" else "Crops"

        out = []
        out.append(self._header_block())
        out.append(_center("SALES RECEIPT", w))
        out.append("")
        out.append(_lr("Invoice:", invoice, w))
        out.append(_lr("Date:", dt, w))
        out.append(_lr("Cashier:", cashier, w))
        out.append(_lr("Status:", status, w))
        if customer_name:
            out.append(_lr("Customer:", _clip(customer_name, w - 10), w))
        out.append(_lr("Ledger:", ledger_label, w))
        out.append("")
        out.append(self._desc_header())

        # --- Items ---
        for it in items:
            name = (getattr(it, "product_name_snapshot", "") or getattr(it, "product_name", "") or "").strip() or "Item"
            qty_str = _qty_label_for_sale_item(it)
            line_total = float(getattr(it, "line_total", 0.0) or 0.0)

            left = f"{_clip(name, 22)} x{qty_str}"
            out.append(_lr(left, _money(line_total), w))

        out.append("-" * w)

        # --- Totals ---
        out.append(_lr("TOTAL:", _money(total), w))
        out.append(_lr("PAID:", _money(paid), w))
        out.append(_lr("BALANCE:", _money(due), w))

        out.append("-" * w)
        out.append(self._footer_block(sale_id))
        return "\n".join(out).rstrip() + "\n"


    # ---------------- Intake receipt (purchase-based) ----------------

    def _receipt_intake(self, intake_id: int, *, cashier_name_override: str = "") -> str:
        from database.models.purchase import Purchase
        from database.models.purchase_item import PurchaseItem
        from database.models.party import Party

        w = int(self.shop.paper_width or 48)

        with get_session() as s:
            pur = s.query(Purchase).filter(Purchase.id == int(intake_id)).first()
            if not pur:
                raise RuntimeError(f"Intake/Purchase not found: id={intake_id}")

            party = None
            try:
                party_id = int(getattr(pur, "party_id", 0) or 0)
                if party_id > 0:
                    party = s.query(Party).filter(Party.id == party_id).first()
            except Exception:
                party = None

            items = (
                s.query(PurchaseItem)
                .filter(PurchaseItem.purchase_id == int(intake_id))
                .order_by(PurchaseItem.id.asc())
                .all()
            )

        dt = _fmt_dt(getattr(pur, "created_at", None))
        total = float(getattr(pur, "total_amount", 0.0) or getattr(pur, "amount_total", 0.0) or 0.0)
        paid = float(getattr(pur, "paid_amount", 0.0) or getattr(pur, "amount_paid", 0.0) or 0.0)
        due = max(0.0, total - paid)
        status = "Paid" if due <= 0 else ("Half" if paid > 0 else "Unpaid")

        cashier = (cashier_name_override or "").strip() or "Cashier"
        invoice = f"PURCHASE-{intake_id:05d}"
        customer_name = (getattr(party, "display_name", "") or "").strip() if party else ""

        # Aggregate weights/bags
        ledger = "crops"
        total_weight = 0.0
        total_bags = 0.0
        for it in items:
            ptype = (getattr(it, "product_type_snapshot", "") or "").strip().lower()
            if ptype in ("fertilizer", "fertilizers"):
                ledger = "fertilizers"
            total_bags += float(getattr(it, "qty_bags", 0) or 0)
            total_weight += float(getattr(it, "total_weight_kg", 0) or getattr(it, "qty_kg", 0) or 0)

        if ledger == "fertilizers":
            total_weight = 0.0  # policy hard-lock

        ledger_label = "Fertilizers" if ledger == "fertilizers" else "Crops"

        out = []
        out.append(self._header_block())
        out.append(_center("INTAKE RECEIPT", w))
        out.append("")
        out.append(_lr("Invoice:", invoice, w))
        out.append(_lr("Date:", dt, w))
        out.append(_lr("Cashier:", cashier, w))
        out.append(_lr("Status:", status, w))
        if customer_name:
            out.append(_lr("Customer:", _clip(customer_name, w - 10), w))
        out.append(_lr("Ledger:", ledger_label, w))
        out.append("")
        out.append(self._desc_header())
        out.append(_lr("Total Purchase Amount:", _money(total), w))
        out.append(_lr("Total Bags:", f"{total_bags:.0f}", w))
        if ledger != "fertilizers":
            out.append(_lr("Total Weight (KG):", f"{total_weight:,.2f} kg", w))

        if status.lower() == "paid":
            out.append(_lr("Amount Paid:", _money(total), w))
        else:
            out.append(_lr("Paid Amount:", _money(paid), w))
            out.append(_lr("Due Balance:", _money(due), w))

        out.append("-" * w)
        out.append(self._footer_block(intake_id))
        return "\n".join(out).rstrip() + "\n"
