"""Add categories table

Revision ID: 0007_add_categories
Revises: 0006_expand_verif_code
Create Date: 2026-08-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_add_categories"
down_revision = "0006_expand_verif_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    
    # Check if table already exists
    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='categories'"
    )
    res = bind.execute(table_check).fetchone()
    if not res:
        op.create_table(
            "categories",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name_ar", sa.String(length=150), nullable=False),
            sa.Column("name_en", sa.String(length=150), nullable=False),
            sa.Column("slug", sa.String(length=100), nullable=False),
            sa.Column("module", sa.String(length=50), nullable=False),
            sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True),
            sa.Column("icon_name", sa.String(length=80), nullable=True),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_categories_slug", "categories", ["slug"], unique=True)
        op.create_index("ix_categories_module", "categories", ["module"], unique=False)
        op.create_index("ix_categories_parent_id", "categories", ["parent_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='categories'"
    )
    if bind.execute(table_check).fetchone():
        op.drop_table("categories")
