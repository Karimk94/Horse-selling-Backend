"""Add equipment_listings and equipment_images tables

Revision ID: 0008_add_equipment_listings
Revises: 0007_add_categories
Create Date: 2026-08-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_add_equipment_listings"
down_revision = "0007_add_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    
    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='equipment_listings'"
    )
    res = bind.execute(table_check).fetchone()
    if not res:
        op.create_table(
            "equipment_listings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("brand", sa.String(length=100), nullable=True),
            sa.Column("sizes", sa.Text(), nullable=True),
            sa.Column("custom_size", sa.String(length=100), nullable=True),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("location_text", sa.String(length=255), nullable=True),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending_review"),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_equipment_listings_owner_id", "equipment_listings", ["owner_id"], unique=False)
        op.create_index("ix_equipment_listings_category_id", "equipment_listings", ["category_id"], unique=False)
        op.create_index("ix_equipment_listings_brand", "equipment_listings", ["brand"], unique=False)
        op.create_index("ix_equipment_listings_status", "equipment_listings", ["status"], unique=False)
        op.create_index("ix_equipment_listings_deleted_at", "equipment_listings", ["deleted_at"], unique=False)

    img_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='equipment_images'"
    )
    if not bind.execute(img_check).fetchone():
        op.create_table(
            "equipment_images",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("equipment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("equipment_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("image_url", sa.String(length=500), nullable=False),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_equipment_images_equipment_id", "equipment_images", ["equipment_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    img_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='equipment_images'"
    )
    if bind.execute(img_check).fetchone():
        op.drop_table("equipment_images")

    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='equipment_listings'"
    )
    if bind.execute(table_check).fetchone():
        op.drop_table("equipment_listings")
