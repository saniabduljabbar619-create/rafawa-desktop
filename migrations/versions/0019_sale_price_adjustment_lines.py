"""Sale price edits: the per-product detail behind a sale price adjustment.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "sale_price_adjustment_lines" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "sale_price_adjustment_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("discount_id", sa.Integer, sa.ForeignKey("sale_discounts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sale_item_id", sa.Integer, sa.ForeignKey("sale_items.id"), nullable=False),
        sa.Column("old_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("basis_qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("basis_unit", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("idx_spa_lines_discount", "sale_price_adjustment_lines", ["discount_id"])
    op.create_index("idx_spa_lines_item", "sale_price_adjustment_lines", ["sale_item_id"])


def downgrade() -> None:
    op.drop_table("sale_price_adjustment_lines")
