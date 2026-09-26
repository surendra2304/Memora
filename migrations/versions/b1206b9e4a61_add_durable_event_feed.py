"""add durable replayable agent event feed

Revision ID: b1206b9e4a61
Revises: 5e89a1b2c3d4
Create Date: 2026-09-26 18:30:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1206b9e4a61"
down_revision: Union[str, None] = "5e89a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("target_agent", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("cloud_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_event_log_event_id", "event_log", ["event_id"], unique=True)
    op.create_index("ix_event_log_event_type", "event_log", ["event_type"], unique=False)
    op.create_index("ix_event_log_tenant_id", "event_log", ["tenant_id"], unique=False)
    op.create_index("ix_event_log_target_agent", "event_log", ["target_agent"], unique=False)
    op.create_index("ix_event_log_cloud_synced", "event_log", ["cloud_synced"], unique=False)
    op.create_index("ix_event_log_created_at", "event_log", ["created_at"], unique=False)
    op.create_index("ix_event_log_tenant_cursor", "event_log", ["tenant_id", "id"], unique=False)
    op.create_index("ix_event_log_target_cursor", "event_log", ["target_agent", "id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_event_log_target_cursor", table_name="event_log")
    op.drop_index("ix_event_log_tenant_cursor", table_name="event_log")
    op.drop_index("ix_event_log_created_at", table_name="event_log")
    op.drop_index("ix_event_log_target_agent", table_name="event_log")
    op.drop_index("ix_event_log_cloud_synced", table_name="event_log")
    op.drop_index("ix_event_log_tenant_id", table_name="event_log")
    op.drop_index("ix_event_log_event_type", table_name="event_log")
    op.drop_index("ix_event_log_event_id", table_name="event_log")
    op.drop_table("event_log")
