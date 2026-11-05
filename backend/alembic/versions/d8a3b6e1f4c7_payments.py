"""payments: payment_requests (link + date hold), hold_waiters, users.billing_config

Revision ID: d8a3b6e1f4c7
Revises: c7f2a9d4b8e1
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd8a3b6e1f4c7'
down_revision: Union[str, None] = 'c7f2a9d4b8e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name: str, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text('now()') if default else None, nullable=nullable)


def upgrade() -> None:
    # Nullable, no backfill: NULL = payments not set up, true of every host today.
    op.add_column('users', sa.Column('billing_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.create_table(
        'payment_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        _ts('created_at', default=True),
        _ts('updated_at', default=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('property_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('call_session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('booking_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('guest_name', sa.String(length=255), nullable=True),
        sa.Column('guest_phone', sa.String(length=32), nullable=True),
        sa.Column('check_in', sa.Date(), nullable=False),
        sa.Column('check_out', sa.Date(), nullable=False),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('amount_due', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        sa.Column('kind', sa.String(length=12), server_default='full', nullable=False),
        sa.Column('confirm_by', sa.String(length=12), server_default='host', nullable=False),
        sa.Column('balance_due_on', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=12), server_default='active', nullable=False),
        sa.Column('razorpay_link_id', sa.String(length=64), nullable=True),
        sa.Column('short_url', sa.String(length=255), nullable=True),
        sa.Column('razorpay_payment_id', sa.String(length=64), nullable=True),
        sa.Column('link_closed', sa.Boolean(), server_default='false', nullable=False),
        _ts('hold_expires_at'),
        _ts('paid_at', nullable=True),
        _ts('link_sent_at', nullable=True),
        _ts('success_sent_at', nullable=True),
        _ts('host_notified_at', nullable=True),
        _ts('conflict_notified_at', nullable=True),
        sa.Column('link_message_sid', sa.String(length=64), nullable=True),
        _ts('link_delivery_checked_at', nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['call_session_id'], ['call_sessions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payment_requests_status_expiry', 'payment_requests', ['status', 'hold_expires_at'])
    op.create_index('ix_payment_requests_user_status', 'payment_requests', ['user_id', 'status'])
    op.create_index('ix_payment_requests_property', 'payment_requests', ['property_id', 'status'])

    op.create_table(
        'hold_waiters',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        _ts('created_at', default=True),
        _ts('updated_at', default=True),
        sa.Column('payment_request_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('guest_name', sa.String(length=255), nullable=True),
        sa.Column('guest_phone', sa.String(length=32), nullable=False),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('call_session_id', postgresql.UUID(as_uuid=True), nullable=True),
        _ts('notified_at', nullable=True),
        sa.ForeignKeyConstraint(['payment_request_id'], ['payment_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['call_session_id'], ['call_sessions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payment_request_id', 'guest_phone', name='uq_hold_waiter_request_phone'),
    )
    op.create_index('ix_hold_waiters_payment_request_id', 'hold_waiters', ['payment_request_id'])


def downgrade() -> None:
    op.drop_index('ix_hold_waiters_payment_request_id', table_name='hold_waiters')
    op.drop_table('hold_waiters')
    op.drop_index('ix_payment_requests_property', table_name='payment_requests')
    op.drop_index('ix_payment_requests_user_status', table_name='payment_requests')
    op.drop_index('ix_payment_requests_status_expiry', table_name='payment_requests')
    op.drop_table('payment_requests')
    op.drop_column('users', 'billing_config')
