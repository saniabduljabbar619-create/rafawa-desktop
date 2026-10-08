"""Quick Purchase orders remember the sale (buyer + cart) they were raised for.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("quick_purchase_orders")}
    with op.batch_alter_table("quick_purchase_orders") as b:      # batch: SQLite cannot ALTER in a foreign key
        if "buyer_party_id" not in cols:
            b.add_column(sa.Column("buyer_party_id", sa.Integer(), nullable=True))
            b.create_foreign_key("fk_qp_orders_buyer", "parties", ["buyer_party_id"], ["id"])
        if "cart_json" not in cols:
            b.add_column(sa.Column("cart_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quick_purchase_orders") as b:
        b.drop_column("cart_json")
        b.drop_column("buyer_party_id")
