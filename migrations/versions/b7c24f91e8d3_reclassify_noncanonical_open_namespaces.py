"""close namespace roots whose legacy type was inferred from a path substring

Revision ID: b7c24f91e8d3
Revises: f35ecb0a7c12
Create Date: 2026-10-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c24f91e8d3"
down_revision: Union[str, Sequence[str], None] = "f35ecb0a7c12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inferred_type(path: str) -> str:
    """Return the post-hardening type for a legacy namespace path."""
    body = path[len("memora://"):] if path.startswith("memora://") else path.lstrip("/")
    segments = body.split("/")
    root = segments[0] if segments else ""
    if root == "public":
        return "public"
    if root == "universe" and len(segments) >= 2 and segments[1] == "global":
        return "universe-global"
    if root in {"team", "shared"} or "team" in segments or "shared" in segments:
        return "team-shared"
    if "private" in segments:
        return "agent-private"
    if len(segments) >= 2 and segments[1] == "projects":
        return "project-private"
    # Unknown or legacy-shaped paths fail closed rather than retaining an
    # openly readable type whose ownership semantics cannot be inferred.
    return "project-private"


def upgrade() -> None:
    """Downgrade stale open types unless the path uses their canonical root."""
    connection = op.get_bind()
    namespaces = sa.Table("namespaces", sa.MetaData(), autoload_with=connection)
    rows = connection.execute(
        sa.select(namespaces.c.id, namespaces.c.path, namespaces.c.type).where(
            namespaces.c.type.in_(("public", "universe-global"))
        )
    ).mappings().all()
    for row in rows:
        expected = _inferred_type(row["path"])
        if expected != row["type"]:
            connection.execute(
                sa.update(namespaces)
                .where(namespaces.c.id == row["id"])
                .values(type=expected)
            )


def downgrade() -> None:
    """Do not restore the unsafe open types corrected by this data migration."""
    # This is intentionally irreversible: restoring prior PUBLIC/GLOBAL labels
    # for noncanonical paths would reintroduce cross-agent disclosure.
