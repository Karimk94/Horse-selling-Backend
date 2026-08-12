"""Add rider_gear_listings and rider_gear_images tables

Revision ID: 0009_add_rider_gear_listings
Revises: 0008_add_equipment_listings
Create Date: 2026-08-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_add_rider_gear_listings"
down_revision = "0008_add_equipment_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    
    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='rider_gear_listings'"
    )
    res = bind.execute(table_check).fetchone()
    if not res:
        op.create_table(
            "rider_gear_listings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("brand", sa.String(length=100), nullable=True),
            sa.Column("gender", sa.String(length=20), nullable=False, server_default="unisex"),
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
        op.create_index("ix_rider_gear_listings_owner_id", "rider_gear_listings", ["owner_id"], unique=False)
        op.create_index("ix_rider_gear_listings_category_id", "rider_gear_listings", ["category_id"], unique=False)
        op.create_index("ix_rider_gear_listings_brand", "rider_gear_listings", ["brand"], unique=False)
        op.create_index("ix_rider_gear_listings_gender", "rider_gear_listings", ["gender"], unique=False)
        op.create_index("ix_rider_gear_listings_status", "rider_gear_listings", ["status"], unique=False)
        op.create_index("ix_rider_gear_listings_deleted_at", "rider_gear_listings", ["deleted_at"], unique=False)

    img_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='rider_gear_images'"
    )
    if not bind.execute(img_check).fetchone():
        op.create_table(
            "rider_gear_images",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("rider_gear_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rider_gear_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("image_url", sa.String(length=500), nullable=False),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_rider_gear_images_rider_gear_id", "rider_gear_images", ["rider_gear_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    img_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='rider_gear_images'"
    )
    if bind.execute(img_check).fetchone():
        op.drop_table("rider_gear_images")

    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='rider_gear_listings'"
    )
    if bind.execute(table_check).fetchone():
        op.drop_table("rider_gear_listings")
