"""Purchase price edits: the per-product detail behind a price adjustment.

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "purchase_price_adjustment_lines" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "purchase_price_adjustment_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("adjustment_id", sa.Integer, sa.ForeignKey("purchase_price_adjustments.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("purchase_item_id", sa.Integer, sa.ForeignKey("purchase_items.id"), nullable=False),
        sa.Column("old_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("new_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("basis_qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("basis_unit", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("idx_ppa_lines_adjustment", "purchase_price_adjustment_lines", ["adjustment_id"])
    op.create_index("idx_ppa_lines_item", "purchase_price_adjustment_lines", ["purchase_item_id"])


def downgrade() -> None:
    op.drop_table("purchase_price_adjustment_lines")
