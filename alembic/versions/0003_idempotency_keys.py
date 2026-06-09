"""Add idempotency keys table

Revision ID: 0003_idempotency_keys
Revises: 0002_offer_transition_audits
Create Date: 2026-04-05 00:00:02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_idempotency_keys"
down_revision = "0002_offer_transition_audits"
branch_labels = None
depends_on = None


def _build_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "idempotency_keys",
        metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("request_key", sa.String(length=255), nullable=False, index=True),
        sa.Column("action", sa.String(length=120), nullable=False, index=True),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_key", "action", name="uq_idempotency_user_key_action"),
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
