"""add booking pricing/attribution fields and price_events

Revision ID: e5b8d2f1a9c3
Revises: c7e2a91d4f10
Create Date: 2026-10-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5b8d2f1a9c3'
down_revision: Union[str, None] = 'c7e2a91d4f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive only. Every new bookings column is nullable or has a server
    # default, so existing rows and the iCal sync keep working unchanged.
    # No historical price is invented: existing bookings get
    # price_status='unknown' / price_source='unknown' with a NULL
    # final_booking_price, and attribution 'unknown'.
    op.add_column('bookings', sa.Column('guest_phone_last4', sa.String(length=4), nullable=True))
    op.add_column(
        'bookings', sa.Column('kind', sa.String(length=16), nullable=False, server_default='reservation')
    )
    op.add_column('bookings', sa.Column('initial_price', sa.Numeric(12, 2), nullable=True))
    op.add_column('bookings', sa.Column('negotiated_price', sa.Numeric(12, 2), nullable=True))
    op.add_column('bookings', sa.Column('final_booking_price', sa.Numeric(12, 2), nullable=True))
    op.add_column('bookings', sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'))
    op.add_column(
        'bookings', sa.Column('price_source', sa.String(length=32), nullable=False, server_default='unknown')
    )
    op.add_column(
        'bookings', sa.Column('price_status', sa.String(length=32), nullable=False, server_default='unknown')
    )
    op.add_column('bookings', sa.Column('price_confirmed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bookings', sa.Column('price_confirmed_by', sa.String(length=16), nullable=True))
    op.add_column('bookings', sa.Column('price_reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'bookings',
        sa.Column('mira_attribution_status', sa.String(length=16), nullable=False, server_default='unknown'),
    )
    op.add_column(
        'bookings',
        sa.Column(
            'mira_attribution_call_session_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('call_sessions.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column(
        'bookings',
        sa.Column(
            'mira_attribution_lead_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('leads.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index('ix_bookings_mira_attribution_lead_id', 'bookings', ['mira_attribution_lead_id'])
    op.add_column(
        'bookings',
        sa.Column('mira_attribution_signals', postgresql.JSONB(), nullable=False, server_default='[]'),
    )
    op.add_column('bookings', sa.Column('mira_attribution_reviewed_at', sa.DateTime(timezone=True), nullable=True))

    # Existing iCal rows whose SUMMARY (stored in guest_name) marks a
    # no-guest block, e.g. Airbnb's "Airbnb (Not available)". This reads data
    # already on the row; it doesn't infer anything new. Same patterns as
    # booking_reconciliation_service.classify_ical_kind.
    op.execute(
        """
        UPDATE bookings SET kind = 'blocked'
        WHERE platform <> 'manual'
          AND (guest_name ILIKE '%not available%' OR guest_name ILIKE '%blocked%' OR guest_name ILIKE 'closed%')
        """
    )

    op.create_table(
        'price_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'property_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('properties.id', ondelete='SET NULL'), nullable=True
        ),
        sa.Column(
            'call_session_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('call_sessions.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'booking_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('bookings.id', ondelete='SET NULL'), nullable=True
        ),
        sa.Column('price_type', sa.String(length=32), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('list_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('guest_offer', sa.Numeric(12, 2), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('nights', sa.Integer(), nullable=True),
        sa.Column('check_in', sa.Date(), nullable=True),
        sa.Column('check_out', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_price_events_user_id', 'price_events', ['user_id'])
    op.create_index('ix_price_events_property_id', 'price_events', ['property_id'])
    op.create_index('ix_price_events_call_session_id', 'price_events', ['call_session_id'])
    op.create_index('ix_price_events_booking_id', 'price_events', ['booking_id'])


def downgrade() -> None:
    op.drop_index('ix_price_events_booking_id', table_name='price_events')
    op.drop_index('ix_price_events_call_session_id', table_name='price_events')
    op.drop_index('ix_price_events_property_id', table_name='price_events')
    op.drop_index('ix_price_events_user_id', table_name='price_events')
    op.drop_table('price_events')

    op.drop_index('ix_bookings_mira_attribution_lead_id', table_name='bookings')
    for column in (
        'mira_attribution_reviewed_at',
        'mira_attribution_signals',
        'mira_attribution_lead_id',
        'mira_attribution_call_session_id',
        'mira_attribution_status',
        'price_reviewed_at',
        'price_confirmed_by',
        'price_confirmed_at',
        'price_status',
        'price_source',
        'currency',
        'final_booking_price',
        'negotiated_price',
        'initial_price',
        'kind',
        'guest_phone_last4',
    ):
        op.drop_column('bookings', column)
