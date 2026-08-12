"""Expand verification_code column length for hashed OTP storage

Revision ID: 0006_expand_verif_code
Revises: 0005_add_soft_delete_to_horses
Create Date: 2026-08-06 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_expand_verif_code"
down_revision = "0005_add_soft_delete_to_horses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "verification_code",
        existing_type=sa.String(length=6),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "verification_code",
        existing_type=sa.String(length=255),
        type_=sa.String(length=6),
        existing_nullable=True,
    )
