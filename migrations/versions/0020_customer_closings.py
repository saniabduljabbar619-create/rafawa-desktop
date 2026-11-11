"""Customer closings: periodic balance closings, net activity, and itemized calculations.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "customer_closings" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "customer_closings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("closing_no", sa.String(32), nullable=False, unique=True),
        sa.Column("party_id", sa.Integer, sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("party_name", sa.String(180), nullable=False),
        sa.Column("party_phone", sa.String(40), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opening_balance", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_purchases", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_sales", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_advance_out", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_advance_in", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_payments_in", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_payments_out", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("total_returns", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("net_period_change", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("closing_balance", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(20), nullable=False, server_default="closed"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("details_json", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_cust_closing_no", "customer_closings", ["closing_no"])
    op.create_index("idx_cust_closing_party", "customer_closings", ["party_id"])
    op.create_index("idx_cust_closing_party_name", "customer_closings", ["party_name"])
    op.create_index("idx_cust_closing_period_end", "customer_closings", ["period_end"])


def downgrade() -> None:
    op.drop_table("customer_closings")
