"""add host_capabilities and host_onboarding

Revision ID: e8c3f1a7b2d9
Revises: d4a7b3e9c2f1
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e8c3f1a7b2d9'
down_revision: Union[str, None] = 'd4a7b3e9c2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive -- see app/models/host_capability.py and
    # app/models/host_onboarding.py. host_capabilities needs no backfill: a
    # missing row resolves to the registry default, which matches pre-
    # capability behavior for every capability.
    op.create_table(
        'host_capabilities',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('capability_id', sa.String(length=64), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('setup_state', sa.String(length=16), nullable=False, server_default='not_started'),
        sa.Column('config', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('enabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('user_id', 'capability_id', name='uq_host_capabilities_user_capability'),
    )
    op.create_index('ix_host_capabilities_user_id', 'host_capabilities', ['user_id'])

    op.create_table(
        'host_onboarding',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='in_progress'),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='onboarding'),
        sa.Column('current_step', sa.String(length=32), nullable=False, server_default='profile'),
        sa.Column('completed_steps', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('selected_capabilities', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('data', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_host_onboarding_user_id', 'host_onboarding', ['user_id'], unique=True)

    # Every host that exists before this migration keeps today's experience:
    # mark them onboarded so the dashboard never redirects them into the new
    # onboarding flow. gen_random_uuid() is core Postgres since 13.
    op.execute(
        """
        INSERT INTO host_onboarding (id, user_id, status, source, current_step, completed_at)
        SELECT gen_random_uuid(), id, 'completed', 'legacy', 'done', now()
        FROM users
        """
    )


def downgrade() -> None:
    op.drop_index('ix_host_onboarding_user_id', table_name='host_onboarding')
    op.drop_table('host_onboarding')
    op.drop_index('ix_host_capabilities_user_id', table_name='host_capabilities')
    op.drop_table('host_capabilities')
