"""Add offer transition audit table

Revision ID: 0002_offer_transition_audits
Revises: 0001_initial_schema
Create Date: 2026-04-05 00:00:01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_offer_transition_audits"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _build_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "offer_transition_audits",
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "offer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "changed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("response_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = _build_table(metadata)
    table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = _build_table(metadata)
    table.drop(bind=bind, checkfirst=True)
