"""Quick Purchase orders remember the sale made when they were confirmed.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "sale_id" in {c["name"] for c in insp.get_columns("quick_purchase_orders")}:
        return
    if {"sales", "parties"} <= set(insp.get_table_names()):
        with op.batch_alter_table("quick_purchase_orders") as b:      # batch: SQLite cannot ALTER in a foreign key
            b.add_column(sa.Column("sale_id", sa.Integer(), nullable=True))
            b.create_foreign_key("fk_qp_orders_sale", "sales", ["sale_id"], ["id"])
    else:                                                            # partial legacy database: plain column
        op.add_column("quick_purchase_orders", sa.Column("sale_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quick_purchase_orders") as b:
        b.drop_column("sale_id")
