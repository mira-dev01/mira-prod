"""add notification preferences and host transfer numbers

Revision ID: a7d2e9c4b1f6
Revises: f2a6c9d4e1b7
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7d2e9c4b1f6'
down_revision: Union[str, None] = 'f2a6c9d4e1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive. '{}' preferences = every default = today's behaviour;
    # NULL transfer numbers = the account number, as today.
    #
    # Deliberately NOT dropping users.whatsapp_assist_enabled /
    # users.follow_up_channel_preference here even though the app no longer
    # maps them: main's code still selects them, and this repo has twice had
    # dev migrations run against the production database. Drop them in the
    # release migration that ships this code to main.
    op.add_column(
        'users',
        sa.Column('notification_preferences', postgresql.JSONB(), nullable=False, server_default='{}'),
    )
    op.add_column('properties', sa.Column('host_transfer_phone', sa.String(length=32), nullable=True))
    op.add_column('call_sessions', sa.Column('handoff_destination_phone', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('call_sessions', 'handoff_destination_phone')
    op.drop_column('properties', 'host_transfer_phone')
    op.drop_column('users', 'notification_preferences')
