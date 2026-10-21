"""A walk-in is never listed as a registered customer or supplier.

0014 created the per-name walk-in rows with the customer / supplier capability flags switched on when they had sales or
purchases, which made them show up in the Customers and Suppliers lists. Walk-ins have their own screen and are
told apart by party_type, so the flags are cleared on every walk-in row.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "parties" not in sa.inspect(bind).get_table_names():
        return
    bind.execute(sa.text("UPDATE parties SET is_customer = 0, is_supplier = 0 WHERE party_type = 'walkin'"))


def downgrade() -> None:
    pass                                        # the flags were wrong; nothing to restore
