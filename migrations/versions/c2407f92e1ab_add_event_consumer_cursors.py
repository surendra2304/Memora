"""add independent durable acknowledgement cursors to the event feed

Revision ID: c2407f92e1ab
Revises: b1206b9e4a61
Create Date: 2026-09-26 00:00:00.000000+00:00

Convergent for the same reason as its parent revision b1206b9e4a61:
`Base.metadata.create_all()` may already have built this table from the ORM
model, and create_all never ALTERs an existing table. A bare
`op.create_table` would then fail with "table already exists" and leave the
database permanently unable to reach head, which is exactly what happened.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c2407f92e1ab"
down_revision: Union[str, None] = "b1206b9e4a61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS: tuple[tuple[str, object, bool, object | None], ...] = (
    # name, type, nullable, server_default
    ("tenant_id", sa.String(length=64), False, None),
    ("agent", sa.String(length=64), False, None),
    ("consumer_id", sa.String(length=64), False, "default"),
    ("last_event_id", sa.Integer(), False, "0"),
    ("updated_at", sa.DateTime(timezone=True), False, None),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "event_consumer_cursors" not in inspector.get_table_names():
        op.create_table(
            "event_consumer_cursors",
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("agent", sa.String(length=64), nullable=False),
            sa.Column("consumer_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("last_event_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("tenant_id", "agent", "consumer_id"),
        )
        return

    present = {c["name"] for c in inspector.get_columns("event_consumer_cursors")}
    for name, coltype, nullable, server_default in _COLUMNS:
        if name not in present:
            op.add_column(
                "event_consumer_cursors",
                sa.Column(name, coltype, nullable=nullable, server_default=server_default),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "event_consumer_cursors" in inspector.get_table_names():
        op.drop_table("event_consumer_cursors")