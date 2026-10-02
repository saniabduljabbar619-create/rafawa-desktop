"""Debt Center: expenses table.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "expenses" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("expense_no", sa.String(20), unique=True, nullable=True),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("paid_to", sa.String(120), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("method", sa.String(30), nullable=True),
        sa.Column("reference", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("active", "voided", name="expense_status_enum"),
                  nullable=False, server_default="active"),
        sa.Column("voided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("void_reason", sa.String(255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_expenses_date", "expenses", ["expense_date"])
    op.create_index("idx_expenses_category", "expenses", ["category"])
    op.create_index("idx_expenses_status", "expenses", ["status"])


def downgrade() -> None:
    op.drop_table("expenses")
