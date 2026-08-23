"""Add location fields to horses table

Revision ID: 0011_add_location_to_horses
Revises: 0010_add_services
Create Date: 2026-08-20 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_add_location_to_horses"
down_revision = "0010_add_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Check if location_text column already exists
    col_check = sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='horses' AND column_name='location_text'"
    )
    if not bind.execute(col_check).fetchone():
        op.add_column("horses", sa.Column("location_text", sa.String(length=255), nullable=True))

    lat_check = sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='horses' AND column_name='latitude'"
    )
    if not bind.execute(lat_check).fetchone():
        op.add_column("horses", sa.Column("latitude", sa.Float(), nullable=True))

    lon_check = sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='horses' AND column_name='longitude'"
    )
    if not bind.execute(lon_check).fetchone():
        op.add_column("horses", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    lon_check = sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='horses' AND column_name='longitude'"
    )
    if bind.execute(lon_check).fetchone():
        op.drop_column("horses", "longitude")

    lat_check = sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='horses' AND column_name='latitude'"
    )
    if bind.execute(lat_check).fetchone():
        op.drop_column("horses", "latitude")

    col_check = sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='horses' AND column_name='location_text'"
    )
    if bind.execute(col_check).fetchone():
        op.drop_column("horses", "location_text")
