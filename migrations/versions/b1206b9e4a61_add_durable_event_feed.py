"""add durable replayable agent event feed

Revision ID: b1206b9e4a61
Revises: 5e89a1b2c3d4
Create Date: 2026-09-26 18:30:00.000000+00:00

Why this migration is convergent rather than a plain CREATE TABLE
----------------------------------------------------------------
`storage/relational/session.py` calls `Base.metadata.create_all()` on startup.
That created `event_log` from an EARLIER version of the ORM model, which had no
`target_agent` and no `cloud_synced`. SQLAlchemy's create_all never ALTERs an
existing table, so those two columns were never added, and a later startup then
failed hard:

    sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
    no such column: event_log.target_agent

`alembic upgrade head` could not repair it either, because a bare
`op.create_table("event_log")` fails with "table already exists".

So this migration is written to converge from whatever shape the table is
actually in: create it if absent, otherwise add only the missing columns and
indexes. Existing rows are preserved — this is a shared memory database and
dropping it is not an option.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1206b9e4a61"
down_revision: Union[str, None] = "5e89a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Columns this revision is responsible for, with the type used to add any
#: that a previously-created table is missing.
_COLUMNS: tuple[tuple[str, object], ...] = (
    ("event_id", sa.String(length=64)),
    ("event_type", sa.String(length=128)),
    ("tenant_id", sa.String(length=64)),
    ("target_agent", sa.String(length=64)),
    ("payload", sa.JSON()),
    ("cloud_synced", sa.Boolean()),
    ("created_at", sa.DateTime(timezone=True)),
)

_INDEXES: tuple[tuple[str, list[str], bool], ...] = (
    ("ix_event_log_event_id", ["event_id"], True),
    ("ix_event_log_event_type", ["event_type"], False),
    ("ix_event_log_tenant_id", ["tenant_id"], False),
    ("ix_event_log_target_agent", ["target_agent"], False),
    ("ix_event_log_cloud_synced", ["cloud_synced"], False),
    ("ix_event_log_created_at", ["created_at"], False),
    ("ix_event_log_tenant_cursor", ["tenant_id", "id"], False),
    ("ix_event_log_target_cursor", ["target_agent", "id"], False),
)


def _existing() -> set[str]:
    """Column names currently present on event_log, or set() if absent."""
    inspector = sa.inspect(op.get_bind())
    if "event_log" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("event_log")}


def _existing_indexes() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "event_log" not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes("event_log")}


def upgrade() -> None:
    present = _existing()

    if not present:
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
        present = {name for name, _ in _COLUMNS}

    # Bring a table that create_all built from an older model up to this
    # revision's shape, without touching the rows it already holds.
    for name, coltype in _COLUMNS:
        if name not in present:
            nullable = name not in {"event_id", "event_type", "tenant_id", "payload", "created_at"}
            server_default = None
            if name == "tenant_id":
                server_default = "default"
            elif name == "cloud_synced":
                server_default = sa.false()
            op.add_column(
                "event_log",
                sa.Column(name, coltype, nullable=nullable, server_default=server_default),
            )
            # Track it, or the index pass below will skip every index that
            # depends on a column this same run just added.
            present.add(name)

    # An older table also lacks the event_id uniqueness the replay cursor needs.
    existing_idx = _existing_indexes()
    for name, cols, unique in _INDEXES:
        if name in existing_idx:
            continue
        # Skip an index whose backing column is genuinely still absent.
        if any(c not in present and c != "id" for c in cols):
            continue
        op.create_index(name, "event_log", cols, unique=unique)


def downgrade() -> None:
    present = _existing()
    if not present:
        return
    for name, _, _ in reversed(_INDEXES):
        if name in _existing_indexes():
            op.drop_index(name, table_name="event_log")
    op.drop_table("event_log")