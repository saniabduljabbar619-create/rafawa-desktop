"""Orders: a sheet for money given / received, and a sheet of per-order expenses (with repayments).

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_KIND = sa.Enum("sales", "purchase", name="order_money_kind_enum")


def upgrade() -> None:
    names = set(sa.inspect(op.get_bind()).get_table_names())
    if "order_money" not in names:
        op.create_table(
            "order_money",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("kind", _KIND, nullable=False),
            sa.Column("partner_id", sa.Integer, nullable=False),
            sa.Column("order_id", sa.Integer, nullable=True),
            sa.Column("direction", sa.Enum("in", "out", name="order_money_dir_enum"), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("method", sa.String(30), nullable=True),
            sa.Column("note", sa.String(255), nullable=True),
            sa.Column("entry_date", sa.Date, nullable=False),
            sa.Column("status", sa.Enum("completed", "applied", "voided", name="order_money_status_enum"),
                      nullable=False, server_default="completed"),
            sa.Column("applied_payment_id", sa.Integer, nullable=True),
            sa.Column("voided_at", sa.DateTime, nullable=True),
            sa.Column("voided_by_user_id", sa.Integer, nullable=True),
            sa.Column("void_reason", sa.String(255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_order_money_partner", "order_money", ["kind", "partner_id"])
        op.create_index("idx_order_money_order", "order_money", ["kind", "order_id"])
    if "order_expenses" not in names:
        op.create_table(
            "order_expenses",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("expense_no", sa.String(20), nullable=True, unique=True),
            sa.Column("kind", _KIND, nullable=False),
            sa.Column("order_id", sa.Integer, nullable=False),
            sa.Column("partner_id", sa.Integer, nullable=False),
            sa.Column("expense_date", sa.Date, nullable=False),
            sa.Column("category", sa.String(60), nullable=False),
            sa.Column("description", sa.String(255), nullable=True),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("paid_to", sa.String(120), nullable=True),
            sa.Column("method", sa.String(30), nullable=True),
            sa.Column("recoverable", sa.Boolean, nullable=False, server_default="1"),
            sa.Column("status", sa.Enum("active", "voided", name="order_expense_status_enum"),
                      nullable=False, server_default="active"),
            sa.Column("voided_at", sa.DateTime, nullable=True),
            sa.Column("voided_by_user_id", sa.Integer, nullable=True),
            sa.Column("void_reason", sa.String(255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_order_expenses_order", "order_expenses", ["kind", "order_id"])
        op.create_index("idx_order_expenses_partner", "order_expenses", ["kind", "partner_id"])
    if "order_expense_recoveries" not in names:
        op.create_table(
            "order_expense_recoveries",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("expense_id", sa.Integer, sa.ForeignKey("order_expenses.id"), nullable=False, index=True),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("method", sa.String(30), nullable=True),
            sa.Column("note", sa.String(255), nullable=True),
            sa.Column("entry_date", sa.Date, nullable=False),
            sa.Column("status", sa.Enum("completed", "voided", name="order_recovery_status_enum"),
                      nullable=False, server_default="completed"),
            sa.Column("voided_at", sa.DateTime, nullable=True),
            sa.Column("voided_by_user_id", sa.Integer, nullable=True),
            sa.Column("void_reason", sa.String(255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    for t in ("order_expense_recoveries", "order_expenses", "order_money"):
        op.drop_table(t)
