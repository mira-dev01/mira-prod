"""rent by the room: properties room_* columns, bookings.stay_scope/rooms_booked

Revision ID: f4b8d2a6c1e9
Revises: d8a3b6e1f4c7
Create Date: 2026-11-06 00:00:00.000000

Additive only. Every existing property keeps room_rental_enabled = false and
every existing booking is an entire-place booking, so nothing changes until a
host turns room rental on for a property.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4b8d2a6c1e9'
down_revision: Union[str, None] = 'd8a3b6e1f4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('room_rental_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('properties', sa.Column('room_base_price', sa.Numeric(10, 2), nullable=True))
    op.add_column('properties', sa.Column('rentable_room_count', sa.Integer(), nullable=True))
    op.add_column('properties', sa.Column('room_max_guests', sa.Integer(), nullable=True))
    op.add_column('properties', sa.Column('room_details', sa.Text(), nullable=True))
    op.add_column('properties', sa.Column('room_ical_url', sa.String(length=1024), nullable=True))
    op.add_column('bookings', sa.Column('stay_scope', sa.String(length=16), server_default='entire_place', nullable=False))
    op.add_column('bookings', sa.Column('rooms_booked', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('bookings', 'rooms_booked')
    op.drop_column('bookings', 'stay_scope')
    op.drop_column('properties', 'room_ical_url')
    op.drop_column('properties', 'room_details')
    op.drop_column('properties', 'room_max_guests')
    op.drop_column('properties', 'rentable_room_count')
    op.drop_column('properties', 'room_base_price')
    op.drop_column('properties', 'room_rental_enabled')
