"""add memory scope tier and per-memory shared allow-list

Revision ID: a1d4c7e9f2b3
Revises: b7c24f91e8d3
Create Date: 2026-10-27

Adds the PRIVATE / SHARED / UNIVERSAL scope tier to ``memory_records`` plus the
``memory_shared_agents`` join table that backs the SHARED allow-list. The DDL is
portable: no JSONB, SERIAL, ON CONFLICT, or PostgreSQL-only index syntax, so the
same revision runs on SQLite, PostgreSQL, and Oracle Autonomous Database.

Existing rows are backfilled from the namespace type they already live in:
  universe-global / public -> UNIVERSAL
  team-shared              -> SHARED  (+ `*` wildcard membership)
  anything else            -> PRIVATE
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "a1d4c7e9f2b3"
down_revision: Union[str, Sequence[str], None] = "b7c24f91e8d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WILDCARD = "*"


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "memory_records" not in tables:
        # Nothing to upgrade: a fresh database is created from ORM metadata.
        return

    columns = {column["name"].lower() for column in inspector.get_columns("memory_records")}
    if "scope" not in columns:
        op.add_column(
            "memory_records",
            sa.Column("scope", sa.String(length=9), nullable=False, server_default="PRIVATE"),
        )
    if "owner_agent" not in columns:
        op.add_column(
            "memory_records",
            sa.Column("owner_agent", sa.String(length=128), nullable=False, server_default="friday"),
        )
    op.create_index("ix_memory_scope_owner", "memory_records", ["scope", "owner_agent"])

    if "memory_shared_agents" not in tables:
        op.create_table(
            "memory_shared_agents",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column(
                "memory_id",
                sa.String(length=64),
                sa.ForeignKey("memory_records.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("agent_name", sa.String(length=128), nullable=False),
            sa.Column("granted_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("memory_id", "agent_name", name="uq_memory_shared_agent"),
        )
        op.create_index(
            "ix_memory_shared_agents_tenant_id", "memory_shared_agents", ["tenant_id"]
        )
        op.create_index(
            "ix_memory_shared_agents_memory_id", "memory_shared_agents", ["memory_id"]
        )
        op.create_index(
            "ix_memory_shared_lookup", "memory_shared_agents", ["tenant_id", "agent_name"]
        )

    # ---- backfill -------------------------------------------------------
    # owner_agent: the owning agent's name, resolved from the FK. The column
    # default ('friday') covers rows whose owner row no longer exists.
    connection.execute(sa.text(
        "UPDATE memory_records SET owner_agent = ("
        "  SELECT LOWER(a.name) FROM agents a WHERE a.id = memory_records.owner_id"
        ") WHERE EXISTS ("
        "  SELECT 1 FROM agents a WHERE a.id = memory_records.owner_id"
        ")"
    ))
    connection.execute(sa.text(
        "UPDATE memory_records SET owner_agent = LOWER(agent_id) "
        "WHERE agent_id IS NOT NULL AND owner_agent = 'friday' AND NOT EXISTS ("
        "  SELECT 1 FROM agents a WHERE a.id = memory_records.owner_id"
        ")"
    ))
    connection.execute(sa.text(
        "UPDATE memory_records SET owner_agent = 'friday' WHERE owner_agent IS NULL"
    ))

    # scope: the namespace type already encoded the historical visibility.
    connection.execute(sa.text(
        "UPDATE memory_records SET scope = 'UNIVERSAL' "
        "WHERE namespace_id IN ("
        "  SELECT id FROM namespaces WHERE type IN ('universe-global', 'public')"
        ")"
    ))
    connection.execute(sa.text(
        "UPDATE memory_records SET scope = 'SHARED' "
        "WHERE namespace_id IN (SELECT id FROM namespaces WHERE type = 'team-shared')"
    ))

    shared_rows = connection.execute(sa.text(
        "SELECT m.id, m.tenant_id FROM memory_records m "
        "WHERE m.scope = 'SHARED' AND NOT EXISTS ("
        "  SELECT 1 FROM memory_shared_agents s WHERE s.memory_id = m.id"
        ")"
    )).fetchall()
    for memory_id, tenant_id in shared_rows:
        connection.execute(
            sa.text(
                "INSERT INTO memory_shared_agents "
                "(id, tenant_id, memory_id, agent_name, granted_by, created_at) "
                "VALUES (:id, :tenant_id, :memory_id, :agent_name, :granted_by, CURRENT_TIMESTAMP)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id or "default",
                "memory_id": memory_id,
                "agent_name": WILDCARD,
                "granted_by": "memora",
            },
        )

    # Drop the temporary server defaults on dialects that support it in place.
    # SQLite keeps them; the ORM always supplies a value, so this is cosmetic.
    if connection.dialect.name in {"oracle", "postgresql"}:
        op.alter_column("memory_records", "scope", server_default=None)
        op.alter_column("memory_records", "owner_agent", server_default=None)


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "memory_shared_agents" in tables:
        op.drop_index("ix_memory_shared_lookup", table_name="memory_shared_agents")
        op.drop_index("ix_memory_shared_agents_memory_id", table_name="memory_shared_agents")
        op.drop_index("ix_memory_shared_agents_tenant_id", table_name="memory_shared_agents")
        op.drop_table("memory_shared_agents")
    if "memory_records" in tables:
        op.drop_index("ix_memory_scope_owner", table_name="memory_records")
        for column in ("owner_agent", "scope"):
            try:
                op.drop_column("memory_records", column)
            except Exception:
                # Older SQLite builds cannot drop a column in place; the
                # downgrade is best-effort there.
                pass
