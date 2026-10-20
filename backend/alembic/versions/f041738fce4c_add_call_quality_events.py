"""add call_quality_events

Revision ID: f041738fce4c
Revises: c4153fd289d2
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f041738fce4c'
down_revision: Union[str, None] = 'c4153fd289d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New table only -- persists app/voice/conversation_quality.py's
    # ValidationResult rows after a call ends (docs/tasks/
    # building-intelligence.md, Implementation 1). Purely additive: no
    # existing table/column touched, no backfill needed since there is no
    # prior data to migrate.
    op.create_table(
        'call_quality_events',
        sa.Column('call_session_id', sa.UUID(), nullable=False),
        sa.Column('rule', sa.String(length=64), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('turn_index', sa.Integer(), nullable=False),
        sa.Column('processing_time_ms', sa.Float(), nullable=False),
        sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['call_session_id'], ['call_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_call_quality_events_call_session_id', 'call_quality_events', ['call_session_id'], unique=False
    )
    op.create_index(
        'ix_call_quality_events_rule_severity', 'call_quality_events', ['rule', 'severity'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_call_quality_events_rule_severity', table_name='call_quality_events')
    op.drop_index('ix_call_quality_events_call_session_id', table_name='call_quality_events')
    op.drop_table('call_quality_events')
