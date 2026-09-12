"""enforce_identity_scope_and_idempotency

Revision ID: 5e89a1b2c3d4
Revises: 433bb01f2a2a
Create Date: 2026-09-11 23:10:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e89a1b2c3d4'
down_revision: Union[str, None] = '433bb01f2a2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('memory_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.String(length=64), nullable=False, server_default='default_user'))
        batch_op.add_column(sa.Column('agent_id', sa.String(length=64), nullable=False, server_default='friday'))
        batch_op.add_column(sa.Column('workspace_id', sa.String(length=64), nullable=False, server_default='default_workspace'))
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=False, server_default='default_device'))
        batch_op.add_column(sa.Column('task_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('idempotency_key', sa.String(length=128), nullable=True))
        
        batch_op.create_index('ix_memory_identity_user', ['tenant_id', 'user_id'], unique=False)
        batch_op.create_index('ix_memory_identity_agent', ['tenant_id', 'agent_id'], unique=False)
        batch_op.create_index('ix_memory_identity_task', ['tenant_id', 'task_id'], unique=False)
        batch_op.create_index('ix_memory_identity_ws', ['tenant_id', 'workspace_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memory_records_idempotency_key'), ['idempotency_key'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('memory_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_records_idempotency_key'))
        batch_op.drop_index('ix_memory_identity_ws')
        batch_op.drop_index('ix_memory_identity_task')
        batch_op.drop_index('ix_memory_identity_agent')
        batch_op.drop_index('ix_memory_identity_user')
        
        batch_op.drop_column('idempotency_key')
        batch_op.drop_column('task_id')
        batch_op.drop_column('device_id')
        batch_op.drop_column('workspace_id')
        batch_op.drop_column('agent_id')
        batch_op.drop_column('user_id')
