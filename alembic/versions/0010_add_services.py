"""Add service_listings, service_images, and service_inquiries tables

Revision ID: 0010_add_services
Revises: 0009_add_rider_gear_listings
Create Date: 2026-08-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_add_services"
down_revision = "0009_add_rider_gear_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    
    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='service_listings'"
    )
    res = bind.execute(table_check).fetchone()
    if not res:
        op.create_table(
            "service_listings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("service_type", sa.String(length=50), nullable=False, server_default="housing_boarding"),
            sa.Column("pricing_type", sa.String(length=30), nullable=False, server_default="fixed"),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("location_text", sa.String(length=255), nullable=True),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("availability_calendar", sa.Text(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending_review"),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_service_listings_provider_id", "service_listings", ["provider_id"], unique=False)
        op.create_index("ix_service_listings_category_id", "service_listings", ["category_id"], unique=False)
        op.create_index("ix_service_listings_service_type", "service_listings", ["service_type"], unique=False)
        op.create_index("ix_service_listings_pricing_type", "service_listings", ["pricing_type"], unique=False)
        op.create_index("ix_service_listings_status", "service_listings", ["status"], unique=False)
        op.create_index("ix_service_listings_deleted_at", "service_listings", ["deleted_at"], unique=False)

    img_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='service_images'"
    )
    if not bind.execute(img_check).fetchone():
        op.create_table(
            "service_images",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("service_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("image_url", sa.String(length=500), nullable=False),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_service_images_service_id", "service_images", ["service_id"], unique=False)

    inq_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='service_inquiries'"
    )
    if not bind.execute(inq_check).fetchone():
        op.create_table(
            "service_inquiries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("service_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("inquirer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("inquirer_name", sa.String(length=150), nullable=False),
            sa.Column("inquirer_phone", sa.String(length=50), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("requested_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_service_inquiries_service_id", "service_inquiries", ["service_id"], unique=False)
        op.create_index("ix_service_inquiries_inquirer_id", "service_inquiries", ["inquirer_id"], unique=False)
        op.create_index("ix_service_inquiries_status", "service_inquiries", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inq_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='service_inquiries'"
    )
    if bind.execute(inq_check).fetchone():
        op.drop_table("service_inquiries")

    img_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='service_images'"
    )
    if bind.execute(img_check).fetchone():
        op.drop_table("service_images")

    table_check = sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='service_listings'"
    )
    if bind.execute(table_check).fetchone():
        op.drop_table("service_listings")
