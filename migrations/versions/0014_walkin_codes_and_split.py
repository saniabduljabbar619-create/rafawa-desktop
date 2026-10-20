"""Every walk-in is their own person: a walk-in code, and the shared walk-in bucket is split up.

parties.walkin_code: WKN-00001 (met at the till) or WT-WKN-00001 (met at the weighing scale).
Existing walk-in rows get a code. Documents that were filed under the shared walk-in account (party 11) and told
apart only by a typed name are moved onto one new walk-in per name (so a name that was shared by two real people
can be split by hand later; at least different names no longer share a profile).

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

SHARED = 11


def _next(codes, prefix):
    top = 0
    for c in codes:
        tail = (c or "")[len(prefix) + 1:] if (c or "").startswith(prefix + "-") else ""
        if tail.isdigit():
            top = max(top, int(tail))
    return f"{prefix}-{top + 1:05d}"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "parties" not in tables:
        return
    if "walkin_code" not in {c["name"] for c in insp.get_columns("parties")}:
        op.add_column("parties", sa.Column("walkin_code", sa.String(24), nullable=True))
        op.create_index("ux_parties_walkin_code", "parties", ["walkin_code"], unique=True)

    codes = [r[0] for r in bind.execute(sa.text("SELECT walkin_code FROM parties WHERE walkin_code IS NOT NULL"))]

    # 1) walk-ins that already have their own row get a code
    rows = bind.execute(sa.text(
        "SELECT id FROM parties WHERE party_type = 'walkin' AND walkin_code IS NULL AND id <> :s ORDER BY id"),
        {"s": SHARED}).fetchall()
    for (pid,) in rows:
        code = _next(codes, "WKN")
        codes.append(code)
        bind.execute(sa.text("UPDATE parties SET walkin_code = :c WHERE id = :i"), {"c": code, "i": pid})

    # 2) the shared account: one new walk-in per typed name
    if not ({"sales", "purchases"} <= tables):
        return
    has_wt = "weigh_tickets" in tables and "walkin_name" in {c["name"] for c in insp.get_columns("weigh_tickets")}
    if not bind.execute(sa.text("SELECT 1 FROM parties WHERE id = :s"), {"s": SHARED}).first():
        return

    def names(sql):
        return [(r[0] or "").strip() for r in bind.execute(sa.text(sql), {"s": SHARED}) if (r[0] or "").strip()]

    weighed = set(names("SELECT walkin_name FROM weigh_tickets WHERE party_id = :s")) if has_wt else set()
    seen = []
    for n in (names("SELECT walkin_name FROM purchases WHERE supplier_party_id = :s ORDER BY id")
              + names("SELECT walkin_name FROM sales WHERE buyer_party_id = :s ORDER BY id")
              + (sorted(weighed) if weighed else [])):
        if n not in seen:
            seen.append(n)

    for n in seen:
        prefix = "WT-WKN" if n in weighed else "WKN"
        code = _next(codes, prefix)
        codes.append(code)
        buys = bind.execute(sa.text(
            "SELECT COUNT(*) FROM sales WHERE buyer_party_id = :s AND walkin_name = :n"), {"s": SHARED, "n": n}).scalar()
        sups = bind.execute(sa.text(
            "SELECT COUNT(*) FROM purchases WHERE supplier_party_id = :s AND walkin_name = :n"),
            {"s": SHARED, "n": n}).scalar()
        bind.execute(sa.text(
            "INSERT INTO parties (party_type, display_name, address, is_active, is_customer, is_supplier, walkin_code, "
            "created_at, updated_at) VALUES ('walkin', :n, 'Walk-in Client', 1, :c, :sp, :code, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"n": n, "c": 1 if buys else 0, "sp": 1 if sups else 0, "code": code})
        new_id = bind.execute(sa.text("SELECT id FROM parties WHERE walkin_code = :c"), {"c": code}).scalar()
        p = {"s": SHARED, "n": n, "new": new_id}
        bind.execute(sa.text("UPDATE purchases SET supplier_party_id = :new WHERE supplier_party_id = :s "
                             "AND walkin_name = :n"), p)
        bind.execute(sa.text("UPDATE sales SET buyer_party_id = :new WHERE buyer_party_id = :s "
                             "AND walkin_name = :n"), p)
        if has_wt:
            bind.execute(sa.text("UPDATE weigh_tickets SET party_id = :new WHERE party_id = :s "
                                 "AND walkin_name = :n"), p)
        if "payments" in tables:
            bind.execute(sa.text(
                "UPDATE payments SET party_id = :new WHERE party_id = :s AND ("
                "(entity_type = 'purchase' AND entity_id IN (SELECT id FROM purchases WHERE supplier_party_id = :new)) OR "
                "(entity_type = 'sale' AND entity_id IN (SELECT id FROM sales WHERE buyer_party_id = :new)))"), p)
        if "purchase_returns" in tables:
            bind.execute(sa.text(
                "UPDATE purchase_returns SET party_id = :new WHERE party_id = :s AND purchase_id IN "
                "(SELECT id FROM purchases WHERE supplier_party_id = :new)"), p)
        if "sale_returns" in tables:
            bind.execute(sa.text(
                "UPDATE sale_returns SET party_id = :new WHERE party_id = :s AND sale_id IN "
                "(SELECT id FROM sales WHERE buyer_party_id = :new)"), p)


def downgrade() -> None:
    with op.batch_alter_table("parties") as b:
        b.drop_index("ux_parties_walkin_code")
        b.drop_column("walkin_code")
