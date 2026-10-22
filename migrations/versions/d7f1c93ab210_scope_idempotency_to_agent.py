"""scope idempotency keys to the writing agent

Revision ID: d7f1c93ab210
Revises: c2407f92e1ab
Create Date: 2026-10-05 16:30:00.000000+00:00

Why this migration exists
-------------------------
`memory_records.idempotency_key` carried a GLOBAL unique constraint, but both
lookup sites (`core/memory/pipeline/write_service.py` and
`core/memory/service.py`) scoped their duplicate check to
`(tenant_id, idempotency_key)` only — never by the writing agent. Every agent in
the mesh shares `tenant_id = "default"`, so the first agent to use a key owned it
for everyone:

    friday  writes key "job-42" -> stored
    intelx  writes key "job-42" -> friday's record handed back,
                                   intelx's own content never persisted

That is simultaneous data loss and cross-agent disclosure. Idempotency must be
per (tenant, agent, key).

Convergent by design
--------------------
`storage/relational/session.py` calls `Base.metadata.create_all()` at startup,
and existing deployments already have the old unique index. This revision
inspects the live index set and converges from whatever it finds, rather than
assuming a clean shape.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7f1c93ab210"
down_revision: Union[str, None] = "c2407f92e1ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The pre-existing global unique index created by revision 5e89a1b2c3d4.
_LEGACY_GLOBAL_INDEX = "ix_memory_records_idempotency_key"

#: The per-identity replacement.
_SCOPED_INDEX = "uq_memory_idempotency_scope"
_SCOPED_COLUMNS = ["tenant_id", "agent_id", "idempotency_key"]


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table) if ix.get("name")}


def upgrade() -> None:
    with op.batch_alter_table("memory_records", schema=None) as batch_op:
        existing = _index_names("memory_records")

        # 1. Drop the global constraint before adding the scoped one, so a
        #    legitimate second agent reusing a key is not rejected mid-rebuild.
        if _LEGACY_GLOBAL_INDEX in existing:
            batch_op.drop_index(_LEGACY_GLOBAL_INDEX)

        # 2. Add the per-identity unique index. Idempotent: create_all may have
        #    already produced it from the updated ORM model.
        if _SCOPED_INDEX not in existing:
            batch_op.create_index(_SCOPED_INDEX, _SCOPED_COLUMNS, unique=True)

        # 3. Keep a non-unique lookup index for the key alone, which the write
        #    pipeline still queries on its own.
        if "ix_memory_records_idempotency_key_lookup" not in existing:
            batch_op.create_index(
                "ix_memory_records_idempotency_key_lookup",
                ["tenant_id", "agent_id", "idempotency_key"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("memory_records", schema=None) as batch_op:
        existing = _index_names("memory_records")
        if "ix_memory_records_idempotency_key_lookup" in existing:
            batch_op.drop_index("ix_memory_records_idempotency_key_lookup")
        if _SCOPED_INDEX in existing:
            batch_op.drop_index(_SCOPED_INDEX)
        if _LEGACY_GLOBAL_INDEX not in existing:
            batch_op.create_index(_LEGACY_GLOBAL_INDEX, ["idempotency_key"], unique=True)
