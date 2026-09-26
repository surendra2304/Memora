"""add independent durable acknowledgement cursors to the event feed

Revision ID: c2407f92e1ab
Revises: b1206b9e4a61
Create Date: 2026-09-26 00:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c2407f92e1ab"
down_revision: Union[str, None] = "b1206b9e4a61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_consumer_cursors",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("consumer_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("last_event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "agent", "consumer_id"),
    )


def downgrade() -> None:
    op.drop_table("event_consumer_cursors")
