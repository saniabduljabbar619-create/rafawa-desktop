"""Offsets: set a party's purchase against their sale (goods swap / settle debt with goods).

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "document_offsets" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "document_offsets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id"), nullable=False),
        sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchases.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="settlement"),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("status", sa.Enum("active", "reversed", name="document_offset_status_enum"),
                  nullable=False, server_default="active"),
        sa.Column("reversed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
        sa.Column("reverse_reason", sa.String(255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_offsets_purchase", "document_offsets", ["purchase_id"])
    op.create_index("idx_offsets_sale", "document_offsets", ["sale_id"])
    op.create_index("idx_offsets_party", "document_offsets", ["party_id"])


def downgrade() -> None:
    op.drop_table("document_offsets")
