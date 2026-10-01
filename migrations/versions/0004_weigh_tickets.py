"""Weigher workspace: weigh_tickets, weigh_ticket_lines, weigh_rounds.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "weigh_tickets" not in existing:
        op.create_table(
            "weigh_tickets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ticket_no", sa.String(20), unique=True, nullable=True),
            sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=False),
            sa.Column("party_role", sa.String(20), nullable=False, server_default="supplier"),
            sa.Column("status", sa.Enum("open", "submitted", "finalized", "cancelled",
                                        name="weigh_ticket_status_enum"), nullable=False, server_default="open"),
            sa.Column("receipt_type", sa.String(20), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("weigher_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("finalized_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("finalized_at", sa.DateTime(), nullable=True),
            sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchases.id"), nullable=True),
            sa.Column("cancelled_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("cancel_reason", sa.String(255), nullable=True),
        )
        op.create_index("idx_weigh_tickets_status", "weigh_tickets", ["status"])
        op.create_index("idx_weigh_tickets_weigher", "weigh_tickets", ["weigher_user_id"])
        op.create_index("idx_weigh_tickets_party", "weigh_tickets", ["party_id"])

    if "weigh_ticket_lines" not in existing:
        op.create_table(
            "weigh_ticket_lines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("weigh_tickets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("area", sa.String(80), nullable=True),
            sa.Column("grade", sa.String(40), nullable=True),
            sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        )
        op.create_index("idx_weigh_lines_ticket", "weigh_ticket_lines", ["ticket_id"])

    if "weigh_rounds" not in existing:
        op.create_table(
            "weigh_rounds",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("line_id", sa.Integer(), sa.ForeignKey("weigh_ticket_lines.id", ondelete="CASCADE"), nullable=False),
            sa.Column("round_no", sa.Integer(), nullable=False),
            sa.Column("bags", sa.Numeric(8, 2), nullable=False),
            sa.Column("weight_kg", sa.Numeric(10, 2), nullable=False),
            sa.Column("recorded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("edited_by_user_id", sa.Integer(), nullable=True),
        )
        op.create_index("idx_weigh_rounds_line", "weigh_rounds", ["line_id"])


def downgrade() -> None:
    op.drop_table("weigh_rounds")
    op.drop_table("weigh_ticket_lines")
    op.drop_table("weigh_tickets")
