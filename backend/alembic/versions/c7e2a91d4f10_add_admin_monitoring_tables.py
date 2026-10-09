"""add admin monitoring tables (usage metering, admin OTP login, service settings)

Revision ID: c7e2a91d4f10
Revises: a3f1c9d24e08
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7e2a91d4f10'
down_revision: Union[str, None] = 'a3f1c9d24e08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: three new tables for the internal /admin panel, no
    # existing table touched. See app/models/service_usage_event.py and
    # app/models/admin.py.
    op.create_table(
        'service_usage_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('service', sa.String(length=32), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column(
            'call_session_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('call_sessions.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_service_usage_events_service_created', 'service_usage_events', ['service', 'created_at'])
    op.create_index('ix_service_usage_events_call_session_id', 'service_usage_events', ['call_session_id'])
    op.create_index('ix_service_usage_events_user_id', 'service_usage_events', ['user_id'])

    op.create_table(
        'admin_login_codes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('code_hash', sa.String(length=128), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_admin_login_codes_email', 'admin_login_codes', ['email'])

    op.create_table(
        'admin_service_settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('service', sa.String(length=32), nullable=False, unique=True),
        sa.Column('currency', sa.String(length=8), nullable=False, server_default='INR'),
        sa.Column('prepaid_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('prepaid_set_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('unit_prices', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('admin_service_settings')
    op.drop_index('ix_admin_login_codes_email', table_name='admin_login_codes')
    op.drop_table('admin_login_codes')
    op.drop_index('ix_service_usage_events_user_id', table_name='service_usage_events')
    op.drop_index('ix_service_usage_events_call_session_id', table_name='service_usage_events')
    op.drop_index('ix_service_usage_events_service_created', table_name='service_usage_events')
    op.drop_table('service_usage_events')
