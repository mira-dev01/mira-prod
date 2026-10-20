"""add notification lead_id and responded_at

Revision ID: 3fae82f7b3d0
Revises: 356d5c923c77
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3fae82f7b3d0'
down_revision: Union[str, None] = '356d5c923c77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: two new nullable columns on the existing
    # notifications table, no data migration, no touched rows.
    #
    # lead_id: only ever set by recovery_service.py/whatsapp_reply_service.py
    # for busy_recovery/busy_recovery_reply notifications (Recovery Analytics'
    # join key back to the recovery Lead). Every pre-existing and future
    # non-recovery notification (whatsapp/escalation/system) stays NULL --
    # see app/models/notification.py's own comment.
    op.add_column(
        'notifications',
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_notifications_lead_id_leads',
        'notifications',
        'leads',
        ['lead_id'],
        ['id'],
        ondelete='SET NULL',
    )
    # responded_at: set once, the first time a notification is marked read
    # (app/services/notification_service.py's mark_read) -- the "host
    # responded" signal Recovery Analytics' Average Host Response metric
    # reads. NULL for every notification that hasn't been read yet, and for
    # every notification that predates this migration (accurate: we have no
    # historical read-timestamp data to backfill).
    op.add_column(
        'notifications',
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('notifications', 'responded_at')
    op.drop_constraint('fk_notifications_lead_id_leads', 'notifications', type_='foreignkey')
    op.drop_column('notifications', 'lead_id')
