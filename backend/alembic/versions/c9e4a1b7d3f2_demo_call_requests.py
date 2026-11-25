"""demo call requests: the marketing site's public "test the agent" calls

Revision ID: c9e4a1b7d3f2
Revises: a7c3e9d5b2f8
Create Date: 2026-11-25 00:00:00.000000

Additive only: one new table. Nothing reads it until DEMO_HOST_EMAIL and
DEMO_CALL_SECRET are set.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c9e4a1b7d3f2'
down_revision: Union[str, None] = 'a7c3e9d5b2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'demo_call_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('ip_hash', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_demo_call_requests_phone_created', 'demo_call_requests', ['phone', 'created_at'])
    op.create_index('ix_demo_call_requests_ip_created', 'demo_call_requests', ['ip_hash', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_demo_call_requests_ip_created', table_name='demo_call_requests')
    op.drop_index('ix_demo_call_requests_phone_created', table_name='demo_call_requests')
    op.drop_table('demo_call_requests')
