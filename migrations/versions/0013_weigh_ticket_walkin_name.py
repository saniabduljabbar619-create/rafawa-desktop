"""Weigh tickets can be for a named walk-in.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "weigh_tickets" not in insp.get_table_names():
        return
    if "walkin_name" in {c["name"] for c in insp.get_columns("weigh_tickets")}:
        return
    op.add_column("weigh_tickets", sa.Column("walkin_name", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("weigh_tickets") as b:
        b.drop_column("walkin_name")
