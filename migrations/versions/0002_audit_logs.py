"""Add the audit_logs table (who changed what).

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "audit_logs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(80), nullable=True),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(255), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
    )
    op.create_index("idx_audit_created", "audit_logs", ["created_at"])
    op.create_index("idx_audit_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("idx_audit_user", "audit_logs", ["user_id"])
    op.create_index("idx_audit_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_table("audit_logs")
