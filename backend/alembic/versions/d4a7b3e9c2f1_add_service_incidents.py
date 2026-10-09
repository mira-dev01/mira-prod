"""add service_incidents (admin health monitoring)

Revision ID: d4a7b3e9c2f1
Revises: e5b8d2f1a9c3
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4a7b3e9c2f1'
down_revision: Union[str, None] = 'e5b8d2f1a9c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive -- see app/models/service_incident.py.
    op.create_table(
        'service_incidents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('environment', sa.String(length=32), nullable=False),
        sa.Column('service', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('current_state', sa.String(length=16), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_error', sa.Text(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sample_call_session_id', sa.String(length=64), nullable=True),
        sa.Column('down_alert_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('degraded_alert_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('alerts_sent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_alert_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('errors_at_last_alert', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('detail', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_service_incidents_env_service_open', 'service_incidents', ['environment', 'service', 'resolved_at'])
    op.create_index('ix_service_incidents_opened_at', 'service_incidents', ['opened_at'])


def downgrade() -> None:
    op.drop_index('ix_service_incidents_opened_at', table_name='service_incidents')
    op.drop_index('ix_service_incidents_env_service_open', table_name='service_incidents')
    op.drop_table('service_incidents')
