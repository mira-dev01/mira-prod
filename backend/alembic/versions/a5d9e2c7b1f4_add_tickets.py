"""tickets: durable follow-ups behind what the agent promises a guest

Revision ID: a5d9e2c7b1f4
Revises: e9b3c7d1a4f2
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a5d9e2c7b1f4'
down_revision: Union[str, None] = 'e9b3c7d1a4f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tickets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('property_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('properties.id', ondelete='SET NULL')),
        sa.Column('call_session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('call_sessions.id', ondelete='SET NULL')),
        sa.Column('notification_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('notifications.id', ondelete='SET NULL')),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('summary', sa.String(length=255), nullable=False),
        sa.Column('detail', sa.Text()),
        sa.Column('urgency', sa.String(length=16), nullable=False, server_default='medium'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='open'),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='tool'),
        sa.Column('resolved_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_tickets_user_status', 'tickets', ['user_id', 'status'])
    op.create_index('ix_tickets_call_session', 'tickets', ['call_session_id'])


def downgrade() -> None:
    op.drop_index('ix_tickets_call_session', table_name='tickets')
    op.drop_index('ix_tickets_user_status', table_name='tickets')
    op.drop_table('tickets')
