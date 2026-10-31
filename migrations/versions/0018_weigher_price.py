"""Weighing: keep the weigher's own price apart from the price admin / cashier confirm.

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "weigh_ticket_lines" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("weigh_ticket_lines")}
    if "weigher_price" not in cols:
        op.add_column("weigh_ticket_lines", sa.Column("weigher_price", sa.Numeric(12, 2), nullable=True))
    # every price recorded so far was the weigher's: it stays as their price
    op.execute("UPDATE weigh_ticket_lines SET weigher_price = unit_price "
               "WHERE weigher_price IS NULL AND unit_price IS NOT NULL")


def downgrade() -> None:
    op.drop_column("weigh_ticket_lines", "weigher_price")
