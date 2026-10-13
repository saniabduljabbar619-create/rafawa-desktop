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
        """Every named walk-in we know of (sales, purchases, and weighings still waiting for the cashier),
        with counts, so one who was weighed but not paid yet can still be found."""
        from database.models.weigh_ticket import WeighTicket

        q = (q or "").strip().lower()
        out = {}

        def _add(name, key, n):
            name = (name or "").strip()
            if not name:
                return
            d = out.setdefault(name.lower(), {"name": name, "sales": 0, "purchases": 0, "pending": 0})
            d[key] += int(n or 0)

        for t in WalkinService.list_traders():
            _add(t["name"], "sales", t["sales"])
            _add(t["name"], "purchases", t["purchases"])
        with get_session() as s:
            rows = (s.query(WeighTicket.walkin_name, func.count(WeighTicket.id))
                    .filter(WeighTicket.walkin_name.isnot(None))
                    .filter(WeighTicket.status.in_(("open", "submitted")))
                    .group_by(WeighTicket.walkin_name).all())
        for name, n in rows:
            _add(name, "pending", n)
        res = [d for k, d in out.items() if not q or q in k]
        res.sort(key=lambda d: d["name"].lower())
        return res[:int(limit)]
