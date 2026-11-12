"""weekend rate toggle on properties + Spacez early check-in / late check-out requests

Revision ID: e8b3d5f1a2c4
Revises: d7a2c4f8b1e3
Create Date: 2026-11-11 00:00:00.000000

Additive only. Every existing property keeps weekend_rate_enabled false (every night costs base_price, as
before). spacez_stay_requests is empty until a Spacez guest with a confirmed booking asks for an early
check-in or late check-out on a call.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e8b3d5f1a2c4'
down_revision: Union[str, None] = 'd7a2c4f8b1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('weekend_rate_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('properties', sa.Column('weekend_price', sa.Numeric(10, 2), nullable=True))
    op.create_table(
        'spacez_stay_requests',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('property_id', sa.UUID(), sa.ForeignKey('properties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_session_id', sa.UUID(), sa.ForeignKey('call_sessions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ticket_id', sa.UUID(), sa.ForeignKey('tickets.id', ondelete='SET NULL'), nullable=True),
        sa.Column('payment_request_id', sa.UUID(), sa.ForeignKey('payment_requests.id', ondelete='SET NULL'), nullable=True),
        sa.Column('guest_name', sa.String(length=255), nullable=True),
        sa.Column('guest_phone', sa.String(length=32), nullable=False),
        sa.Column('check_in', sa.Date(), nullable=False),
        sa.Column('check_out', sa.Date(), nullable=False),
        sa.Column('want_early_checkin', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('want_late_checkout', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('early_fee', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('late_fee', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('razorpay_link_id', sa.String(length=64), nullable=True),
        sa.Column('short_url', sa.String(length=255), nullable=True),
        sa.Column('razorpay_payment_id', sa.String(length=64), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_spacez_stay_requests_user_status', 'spacez_stay_requests', ['user_id', 'status'])
    op.create_index('ix_spacez_stay_requests_ticket', 'spacez_stay_requests', ['ticket_id'])


def downgrade() -> None:
    op.drop_index('ix_spacez_stay_requests_ticket', table_name='spacez_stay_requests')
    op.drop_index('ix_spacez_stay_requests_user_status', table_name='spacez_stay_requests')
    op.drop_table('spacez_stay_requests')
    op.drop_column('properties', 'weekend_price')
    op.drop_column('properties', 'weekend_rate_enabled')
