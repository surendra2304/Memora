"""track deletion convergence for the Turso write-through replica

Revision ID: f35ecb0a7c12
Revises: e4a1c7f90b23
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f35ecb0a7c12"
down_revision: Union[str, Sequence[str], None] = "e4a1c7f90b23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Track whether an optional remote memory replica observed each delete.

    Pre-existing tombstones are marked complete: older versions did not record
    whether the optional Turso replica was configured at deletion time. New
    hard-delete requests set the flag explicitly when a replica is active.
    """
    with op.batch_alter_table("deletion_tombstones") as batch:
        batch.add_column(
            sa.Column(
                "turso_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("deletion_tombstones") as batch:
        batch.drop_column("turso_deleted")
