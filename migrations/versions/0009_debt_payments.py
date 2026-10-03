"""Debt Center: payments are saved per debt row (debt_payments); old "paid" amounts move across.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "debt_payments" in insp.get_table_names():
        return
    op.create_table(
        "debt_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("debt_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("method", sa.String(30), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_removed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("removed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("removed_reason", sa.String(255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_debt_payments_entry", "debt_payments", ["entry_id"])

    # amounts typed in the old "paid" column become real payments on the same row; the column is then
    # left at 0 (it is no longer read)
    if "paid" in {c["name"] for c in insp.get_columns("debt_entries")}:
        bind.execute(sa.text(
            "INSERT INTO debt_payments (entry_id, pay_date, amount, method, note, is_removed, removed_by_user_id, "
            "removed_at, removed_reason, created_by_user_id, created_at) "
            "SELECT id, entry_date, paid, method, note, is_removed, removed_by_user_id, removed_at, removed_reason, "
            "created_by_user_id, created_at FROM debt_entries WHERE paid > 0"))
        bind.execute(sa.text("UPDATE debt_entries SET paid = 0 WHERE paid > 0"))


def downgrade() -> None:
    op.drop_table("debt_payments")
