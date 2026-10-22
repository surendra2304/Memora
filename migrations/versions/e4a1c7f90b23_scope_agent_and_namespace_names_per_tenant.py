"""scope agent and namespace names per tenant

Revision ID: e4a1c7f90b23
Revises: d7f1c93ab210
Create Date: 2026-10-05

`agents.name` and `namespaces.path` carried single-column UNIQUE constraints in
the SQLAlchemy model while IdentityService looked both up by (name, tenant_id)
and (path, tenant_id). The result was that a second tenant could not register an
agent name or namespace path a first tenant already used — SQLite raised
"UNIQUE constraint failed: agents.name".

The model now declares composite uniqueness on (tenant_id, name) and
(tenant_id, path). This migration brings migrated databases in line.

Note on existing state: on migrated databases the global uniqueness is not a
table constraint but a standalone unique index. Verified at revision
d7f1c93ab210, `sqlite_master` holds:

    CREATE UNIQUE INDEX ix_agents_name ON agents (name)
    CREATE UNIQUE INDEX ix_namespaces_path ON namespaces (path)

Those indexes — not the CREATE TABLE body — are what raise the IntegrityError,
and batch_alter_table preserves them, so they have to be dropped explicitly.
Databases built by Base.metadata.create_all instead express the same thing as a
column-level unique=True, which the model change already fixes; this revision
tolerates both shapes.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a1c7f90b23"
down_revision: Union[str, Sequence[str], None] = "d7f1c93ab210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The global uniqueness lives in *unique indexes* created by the original
#: schema migration (verified at revision d7f1c93ab210:
#: "CREATE UNIQUE INDEX ix_agents_name ON agents (name)" and
#: "CREATE UNIQUE INDEX ix_namespaces_path ON namespaces (path)"). They are not
#: table constraints, so they must be dropped as indexes. The model keeps
#: index=True on both columns, so each is recreated as a plain index.
_LEGACY_UNIQUE_INDEXES = (
    ("agents", "ix_agents_name"),
    ("namespaces", "ix_namespaces_path"),
)

#: Constraint names a create_all-built database may carry instead.
_LEGACY_CONSTRAINTS = (
    ("agents", ("uq_agents_name", "agents_name_key")),
    ("namespaces", ("uq_namespaces_path", "namespaces_path_key")),
)


def _drop_legacy(table: str, names) -> None:
    """Best-effort removal of the old single-column constraints.

    Absent on some builds, so failures here are expected and ignored.
    """
    for name in names:
        try:
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(name, type_="unique")
        except Exception:
            continue


def upgrade() -> None:
    for table, index_name in _LEGACY_UNIQUE_INDEXES:
        try:
            op.drop_index(index_name, table_name=table)
        except Exception:
            pass
        # Keep the lookup fast now that uniqueness is composite.
        op.create_index(index_name, table, [index_name.split("_", 2)[-1]], unique=False)

    for table, names in _LEGACY_CONSTRAINTS:
        _drop_legacy(table, names)

    with op.batch_alter_table("agents") as batch:
        batch.create_unique_constraint("uq_agents_tenant_name", ["tenant_id", "name"])
    with op.batch_alter_table("namespaces") as batch:
        batch.create_unique_constraint("uq_namespaces_tenant_path", ["tenant_id", "path"])


def downgrade() -> None:
    with op.batch_alter_table("namespaces") as batch:
        batch.drop_constraint("uq_namespaces_tenant_path", type_="unique")
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint("uq_agents_tenant_name", type_="unique")

    for table, index_name in _LEGACY_UNIQUE_INDEXES:
        try:
            op.drop_index(index_name, table_name=table)
        except Exception:
            pass
        op.create_index(index_name, table, [index_name.split("_", 2)[-1]], unique=True)
