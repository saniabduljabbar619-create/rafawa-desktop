"""Debt Center: standalone creditor/debtor book (debt_people, debt_entries) and expenditures.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "debt_people" not in existing:
        op.create_table(
            "debt_people",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("kind", sa.Enum("creditor", "debtor", name="debt_person_kind_enum"), nullable=False),
            sa.Column("name", sa.String(150), nullable=False),
            sa.Column("phone", sa.String(40), nullable=True),
            sa.Column("address", sa.String(255), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_debt_people_kind", "debt_people", ["kind"])
        op.create_index("idx_debt_people_name", "debt_people", ["name"])

    if "debt_entries" not in existing:
        op.create_table(
            "debt_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("person_id", sa.Integer(), sa.ForeignKey("debt_people.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entry_date", sa.Date(), nullable=False),
            sa.Column("details", sa.String(255), nullable=False),
            sa.Column("owed", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("paid", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("method", sa.String(30), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("is_removed", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("removed_by_user_id", sa.Integer(), nullable=True),
            sa.Column("removed_at", sa.DateTime(), nullable=True),
            sa.Column("removed_reason", sa.String(255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_debt_entries_person", "debt_entries", ["person_id"])
        op.create_index("idx_debt_entries_date", "debt_entries", ["entry_date"])

    if "expenditures" not in existing:
        op.create_table(
            "expenditures",
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
            # the enum type already exists from migration 0005 on PostgreSQL-like backends
            sa.Column("status", sa.Enum("active", "voided", name="expense_status_enum"),
                      nullable=False, server_default="active"),
            sa.Column("voided_by_user_id", sa.Integer(), nullable=True),
            sa.Column("voided_at", sa.DateTime(), nullable=True),
            sa.Column("void_reason", sa.String(255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("idx_expenditures_date", "expenditures", ["expense_date"])
        op.create_index("idx_expenditures_category", "expenditures", ["category"])
        op.create_index("idx_expenditures_status", "expenditures", ["status"])


def downgrade() -> None:
    op.drop_table("expenditures")
    op.drop_table("debt_entries")
    op.drop_table("debt_people")
