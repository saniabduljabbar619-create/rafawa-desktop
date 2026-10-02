"""Orders: review notes and post-delivery corrections.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "order_reviews" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "order_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("is_correction", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("rej_before", sa.Numeric(14, 2), nullable=True),
        sa.Column("rej_after", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_before", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_after", sa.Numeric(14, 2), nullable=True),
        sa.Column("total_before", sa.Numeric(14, 2), nullable=True),
        sa.Column("total_after", sa.Numeric(14, 2), nullable=True),
        sa.Column("stock_moved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_order_reviews_order", "order_reviews", ["order_type", "order_id"])


def downgrade() -> None:
    op.drop_table("order_reviews")
