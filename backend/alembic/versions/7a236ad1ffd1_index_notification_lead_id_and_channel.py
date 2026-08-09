"""index notification lead_id and channel

Revision ID: 7a236ad1ffd1
Revises: 3fae82f7b3d0
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7a236ad1ffd1'
down_revision: Union[str, None] = '3fae82f7b3d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 7 audit fix: GET /analytics/recovery (app/api/v1/analytics.py)
    # runs 4 separate queries per page load, every one of them filtering
    # notifications.channel and/or joining on notifications.lead_id -- with
    # no index on either column, each of those was a full sequential scan.
    # channel is also filtered by the pre-existing escalated_calls metric in
    # analytics_summary/analytics_timeseries, so this benefits those too.
    # CONCURRENTLY needs to run outside the migration's transaction.
    with op.get_context().autocommit_block():
        op.create_index(
            'ix_notifications_lead_id', 'notifications', ['lead_id'], unique=False, postgresql_concurrently=True
        )
        op.create_index(
            'ix_notifications_channel', 'notifications', ['channel'], unique=False, postgresql_concurrently=True
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('ix_notifications_channel', table_name='notifications', postgresql_concurrently=True)
        op.drop_index('ix_notifications_lead_id', table_name='notifications', postgresql_concurrently=True)
