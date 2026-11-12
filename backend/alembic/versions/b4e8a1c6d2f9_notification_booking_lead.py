"""notifications.booking_lead_id: a request from a guest who has an active booking

Revision ID: b4e8a1c6d2f9
Revises: a9d4c7e2b6f1
Create Date: 2026-11-11 00:00:00.000000

Additive: NULL for every existing notification.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8a1c6d2f9'
down_revision: Union[str, None] = 'a9d4c7e2b6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('booking_lead_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_notifications_booking_lead_id', 'notifications', 'leads', ['booking_lead_id'], ['id'], ondelete='SET NULL'
    )
    op.create_index('ix_notifications_booking_lead_id', 'notifications', ['booking_lead_id'])


def downgrade() -> None:
    op.drop_index('ix_notifications_booking_lead_id', table_name='notifications')
    op.drop_constraint('fk_notifications_booking_lead_id', 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'booking_lead_id')
