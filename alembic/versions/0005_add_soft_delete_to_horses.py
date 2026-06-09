"""Add soft delete (deleted_at) to Horse model

Revision ID: 0005_add_soft_delete_to_horses
Revises: 0004_push_delivery_logs
Create Date: 2026-04-05 00:00:05
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_add_soft_delete_to_horses"
down_revision = "0004_push_delivery_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    
    # Add deleted_at column if it doesn't exist
    inspector = sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='horses' AND column_name='deleted_at'")
    result = bind.execute(inspector)
    
    if not result.fetchone():
        op.add_column(
            "horses",
            sa.Column(
                "deleted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    # Add index if it doesn't exist
    index_check = sa.text("SELECT indexname FROM pg_indexes WHERE tablename='horses' AND indexname='ix_horses_deleted_at'")
    index_result = bind.execute(index_check)
    if not index_result.fetchone():
        op.create_index("ix_horses_deleted_at", "horses", ["deleted_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    index_check = sa.text("SELECT indexname FROM pg_indexes WHERE tablename='horses' AND indexname='ix_horses_deleted_at'")
    index_result = bind.execute(index_check)
    if index_result.fetchone():
        op.drop_index("ix_horses_deleted_at", table_name="horses")

    inspector = sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='horses' AND column_name='deleted_at'")
    result = bind.execute(inspector)
    
    if result.fetchone():
        op.drop_column("horses", "deleted_at")
