"""Quick Purchase orders: status (draft/pending/completed/cancelled), lines and supplier routing.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "quick_purchase_orders" not in existing:
        op.create_table(
            "quick_purchase_orders",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("order_no", sa.String(20), unique=True, nullable=True),
            sa.Column("status", sa.Enum("draft", "pending", "completed", "cancelled",
                                        name="quick_purchase_status_enum"), nullable=False, server_default="draft"),
            sa.Column("customer_name", sa.String(120), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("close_reason", sa.String(255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_qp_orders_status", "quick_purchase_orders", ["status"])

    if "quick_purchase_lines" not in existing:
        op.create_table(
            "quick_purchase_lines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("quick_purchase_orders.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("qty_bags", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_weight_kg", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=True),
            sa.Column("state", sa.String(12), nullable=False, server_default="open"),
            sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchases.id"), nullable=True),
        )
        op.create_index("idx_qp_lines_order", "quick_purchase_lines", ["order_id"])

    if "quick_purchase_routes" not in existing:
        op.create_table(
            "quick_purchase_routes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("line_id", sa.Integer(), sa.ForeignKey("quick_purchase_lines.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=False),
            sa.Column("outcome", sa.String(12), nullable=False, server_default="asked"),
            sa.Column("note", sa.String(255), nullable=True),
            sa.Column("by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_qp_routes_line", "quick_purchase_routes", ["line_id"])


def downgrade() -> None:
    for t in ("quick_purchase_routes", "quick_purchase_lines", "quick_purchase_orders"):
        op.drop_table(t)
