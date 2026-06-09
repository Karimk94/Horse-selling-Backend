"""Add push delivery logs table

Revision ID: 0004_push_delivery_logs
Revises: 0003_idempotency_keys
Create Date: 2026-04-05 00:00:03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_push_delivery_logs"
down_revision = "0003_idempotency_keys"
branch_labels = None
depends_on = None


def _build_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "push_delivery_logs",
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
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
