from database.session import get_session
from sqlalchemy import text
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