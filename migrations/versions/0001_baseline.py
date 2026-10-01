"""Baseline schema.

Existing installations already have their tables (created by init_db). For those
this migration does nothing - it simply becomes the starting point
(`alembic upgrade head` records it). On a brand-new, empty database it creates the
whole schema from the models as of this release.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

from database.base import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "users" in sa.inspect(bind).get_table_names():
        return                                   # existing installation: nothing to do
    # Fresh database: build the baseline schema. Tables added by later
    # migrations are excluded so their own revisions create them.
    later = {"audit_logs"}   # (return tables are created in their final DECIMAL form; 0003 is then a no-op)
    tables = [t for t in Base.metadata.sorted_tables if t.name not in later]
    Base.metadata.create_all(bind=bind, tables=tables)


def downgrade() -> None:
    raise NotImplementedError("The baseline cannot be downgraded.")
