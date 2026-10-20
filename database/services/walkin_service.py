from database.session import get_session
from sqlalchemy import func, text
from shared.config.constants import WALKIN_PARTY_ID
from database.services.walkin_account import is_shared_walkin

TILL_PREFIX = "WKN"             # met at the till (sales / purchases):   WKN-00001
WEIGH_PREFIX = "WT-WKN"         # met at the weighing scale:             WT-WKN-00001


class WalkinService:

    WALKIN_PARTY_ID = WALKIN_PARTY_ID

    # ------------------------------------------------------------------ identities
    @staticmethod
    def next_code(s, prefix: str) -> str:
        """The next free walk-in code in a series: WKN-00001, WKN-00002 ... (WT-WKN-00001 for the weighing scale)."""
        from database.models.party import Party
        top = 0
        for (c,) in s.query(Party.walkin_code).filter(Party.walkin_code.like(f"{prefix}-%")).all():
            tail = (c or "")[len(prefix) + 1:]
            if tail.isdigit():
                top = max(top, int(tail))
        return f"{prefix}-{top + 1:05d}"

    @staticmethod
    def create(name: str, phone: str = "", source: str = "till", session=None) -> dict:
        """A brand-new walk-in with their own ID. A name is enough; phone is optional and never identifies anyone."""
        from database.models.party import Party
        name = (name or "").strip()
        if not name:
            raise ValueError("Type the walk-in's name first.")
        prefix = WEIGH_PREFIX if source == "weighing" else TILL_PREFIX

        def _make(s):
            row = Party(party_type="walkin", display_name=name, phone=(phone or "").strip() or None,
                        address="Walk-in Client", is_active=True, is_customer=False, is_supplier=False,
                        walkin_code=WalkinService.next_code(s, prefix))
            s.add(row)
            s.flush()
            return {"id": int(row.id), "code": row.walkin_code, "name": row.display_name, "phone": row.phone or "",
                    "source": source}

        if session is not None:
            return _make(session)
        with get_session() as s:
            out = _make(s)
            s.commit()
            return out

    @staticmethod
    def code_of(party_id) -> str:
        from database.models.party import Party
        with get_session() as s:
            row = s.query(Party.walkin_code).filter(Party.id == int(party_id or 0)).first()
        return (row[0] or "") if row else ""

    @staticmethod
    def search(q: str = "", limit: int = 40) -> list:
        """
        Individual walk-ins matching a name, phone, walk-in code (WKN-00012 / WT-WKN-00001), their ID number, or
        the number of one of their weighings (WT-00012). Nothing typed: nothing (the list can be long).
        """
        import re
        from database.models.party import Party
        from database.models.weigh_ticket import WeighTicket

        q = (q or "").strip()
        if not q:
            return []
        low = q.lower()
        digits = re.sub(r"\D", "", q)
        with get_session() as s:
            ids = set()
            like = f"%{q}%"
            for (i,) in s.query(Party.id).filter(Party.party_type == "walkin", Party.id != WALKIN_PARTY_ID).filter(
                    (Party.display_name.ilike(like)) | (Party.phone.ilike(like)) | (Party.walkin_code.ilike(like))):
                ids.add(int(i))
            if digits:
                n = int(digits)
                if low.isdigit() or low.startswith("wt"):
                    for (pid,) in s.query(WeighTicket.party_id).filter(WeighTicket.id == n):
                        ids.add(int(pid))                    # a weighing's number leads to that person
                if low.isdigit():
                    ids.add(n)                               # or their own ID number
            rows = []
            if ids:
                rows = (s.query(Party).filter(Party.id.in_(ids), Party.party_type == "walkin",
                                              Party.id != WALKIN_PARTY_ID).order_by(Party.display_name.asc()).all())
            return [{"id": int(p.id), "code": p.walkin_code or "", "name": p.display_name, "phone": p.phone or "",
                     "source": "weighing" if (p.walkin_code or "").startswith(WEIGH_PREFIX) else "till"}
                    for p in rows][:int(limit)]

    @staticmethod
    def weighings_for(party_ids) -> dict:
        """{party_id: [(weighing no, status), ...]} for several walk-ins at once, oldest first."""
        from database.models.weigh_ticket import WeighTicket
        ids = [int(i) for i in party_ids]
        out = {i: [] for i in ids}
        if not ids:
            return out
        with get_session() as s:
            for pid, tid, tno, status in (s.query(WeighTicket.party_id, WeighTicket.id, WeighTicket.ticket_no,
                                                  WeighTicket.status)
                                          .filter(WeighTicket.party_id.in_(ids)).filter(WeighTicket.status != "cancelled")
                                          .order_by(WeighTicket.id.asc()).all()):
                out[int(pid)].append((tno or f"WT-{int(tid):05d}", status))
        return out

    @staticmethod
    def weighings_of(party_id: int) -> list:
        """The weighing numbers of one walk-in, oldest first."""
        from database.models.weigh_ticket import WeighTicket
        with get_session() as s:
            return [tno or f"WT-{int(tid):05d}" for tid, tno in
                    s.query(WeighTicket.id, WeighTicket.ticket_no).filter(WeighTicket.party_id == int(party_id))
                    .filter(WeighTicket.status != "cancelled").order_by(WeighTicket.id.asc()).all()]

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
