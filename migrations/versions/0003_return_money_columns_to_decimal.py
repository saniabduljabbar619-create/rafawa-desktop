"""Store return amounts/quantities as exact decimals instead of floats.

sale_returns / purchase_returns were the only tables keeping money in FLOAT
columns (everything else already uses DECIMAL), which accumulates rounding
error when thousands of returns are summed. Existing values are converted in
place by the database (FLOAT -> DECIMAL rounds to the new scale).

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# table -> [(column, new type)]
CHANGES = {
    "sale_returns": [
        ("unit_price_snapshot", sa.Numeric(12, 4)),
        ("qty_kg", sa.Numeric(10, 2)),
        ("amount", sa.Numeric(14, 2)),
    ],
    "purchase_returns": [
        ("unit_cost_snapshot", sa.Numeric(12, 4)),
        ("qty_kg", sa.Numeric(10, 2)),
        ("amount", sa.Numeric(14, 2)),
    ],
}


def upgrade() -> None:
    for table, cols in CHANGES.items():
        with op.batch_alter_table(table) as batch:
            for name, new_type in cols:
                batch.alter_column(name, existing_type=sa.Float(), type_=new_type,
                                   existing_nullable=False, existing_server_default=None)


def downgrade() -> None:
    for table, cols in CHANGES.items():
        with op.batch_alter_table(table) as batch:
            for name, old_type in cols:
                batch.alter_column(name, existing_type=old_type, type_=sa.Float(),
                                   existing_nullable=False)
