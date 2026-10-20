from database.session import get_session
from sqlalchemy import func, text
from shared.config.constants import WALKIN_PARTY_ID
from database.services.walkin_account import is_shared_walkin

class WalkinService:

    WALKIN_PARTY_ID = WALKIN_PARTY_ID

    @staticmethod
    def list_traders():

        with get_session() as s:

            # no shared walk-in account (its id belongs to a real customer): nobody to list
            if not is_shared_walkin(WalkinService.WALKIN_PARTY_ID, s):
                return []

            rows = s.execute(text("""
                SELECT
                    walkin_name,
                    SUM(sales_count) AS sales,
                    SUM(purchase_count) AS purchases
                FROM (

                    SELECT
                        walkin_name,
                        COUNT(*) AS sales_count,
                        0 AS purchase_count
                    FROM sales
                    WHERE buyer_party_id = :pid
                    GROUP BY walkin_name

                    UNION ALL

                    SELECT
                        walkin_name,
                        0 AS sales_count,
                        COUNT(*) AS purchase_count
                    FROM purchases
                    WHERE supplier_party_id = :pid
                    GROUP BY walkin_name

                ) x
                GROUP BY walkin_name
                ORDER BY walkin_name
            """), {"pid": WalkinService.WALKIN_PARTY_ID}).fetchall()

        return [
            {
                "name": r.walkin_name,
                "sales": int(r.sales or 0),
                "purchases": int(r.purchases or 0)
            }
            for r in rows
        ]

    @staticmethod
    def list_named(q: str = "", limit: int = 50):
        """Every named walk-in we know of (sales, purchases, and weighings), with counts and weighing numbers, so
        one who was weighed but not paid yet can still be found - by name, or by weighing ID (12, WT-00012)."""
        import re
        from database.models.weigh_ticket import WeighTicket

        q = (q or "").strip().lower()
        digits = re.sub(r"\D", "", q)
        out = {}

        def _row(name):
            name = (name or "").strip()
            if not name:
                return None
            return out.setdefault(name.lower(), {"name": name, "sales": 0, "purchases": 0, "pending": 0, "refs": []})

        for t in WalkinService.list_traders():
            d = _row(t["name"])
            if d:
                d["sales"] += int(t["sales"] or 0)
                d["purchases"] += int(t["purchases"] or 0)
        id_hits = set()                                    # names whose weighing matches the ID typed
        with get_session() as s:
            rows = (s.query(WeighTicket.id, WeighTicket.ticket_no, WeighTicket.walkin_name, WeighTicket.status)
                    .filter(WeighTicket.walkin_name.isnot(None)).filter(WeighTicket.status != "cancelled")
                    .order_by(WeighTicket.id.asc()).all())
        for tid, tno, name, status in rows:
            d = _row(name)
            if not d:
                continue
            ref = tno or f"WT-{int(tid):05d}"
            d["refs"].append(ref)
            if status in ("open", "submitted"):
                d["pending"] += 1
            if q and ((digits and int(digits) == int(tid) and (q.isdigit() or q.startswith("wt")))
                      or (q.startswith("wt") and q in ref.lower())):
                id_hits.add(name.strip().lower())
        res = [d for k, d in out.items() if not q or q in k or k in id_hits]
        res.sort(key=lambda d: d["name"].lower())
        return res[:int(limit)]

    @staticmethod
    def totals(name: str):
        """Still to pay / paid (purchases) and still to receive / received (sales) for ONE walk-in, by name."""
        from database.models.purchase import Purchase
        from database.models.sale import Sale
        from database.services.document_balance import document_balances

        out = {"payable": 0.0, "paid": 0.0, "receivable": 0.0, "received": 0.0}
        name = (name or "").strip()
        if not name:
            return out
        pid = WalkinService.WALKIN_PARTY_ID
        with get_session() as s:
            pur = [r[0] for r in s.query(Purchase.id).filter(
                Purchase.supplier_party_id == pid, Purchase.walkin_name == name,
                Purchase.status != "voided").all()]
            sal = [r[0] for r in s.query(Sale.id).filter(
                Sale.buyer_party_id == pid, Sale.walkin_name == name, Sale.status != "voided").all()]
            for b in document_balances(s, "purchase", pur).values():
                out["payable"] += b.balance
                out["paid"] += min(b.settled, b.net_total)
            for b in document_balances(s, "sale", sal).values():
                out["receivable"] += b.balance
                out["received"] += min(b.settled, b.net_total)
        return {k: round(v, 2) for k, v in out.items()}
