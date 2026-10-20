"""add lead recovery metadata (entry_channel, recovery_reason)

Revision ID: 356d5c923c77
Revises: 6384600c83f2
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '356d5c923c77'
down_revision: Union[str, None] = '6384600c83f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive: two new columns on the existing leads table, no data
    # migration, no touched columns. Does NOT add a new Lead.status value --
    # see app/models/lead.py's own comment for why this is metadata
    # (how/why a lead entered), not a sales-pipeline stage.
    #
    # entry_channel: NOT NULL with a server_default, same pattern as the
    # existing lead_source/status columns -- every pre-existing row backfills
    # to "phone_call" (accurate: every lead in this table today came from a
    # voice call, the only entry point that has ever existed) with no
    # separate UPDATE step needed.
    op.add_column(
        'leads',
        sa.Column('entry_channel', sa.String(length=32), nullable=False, server_default='phone_call'),
    )
    # recovery_reason: nullable, no default -- most leads are NOT recovery
    # leads (a normal answered call needs no explanation for how it entered),
    # so NULL is the correct/common value, not a placeholder to backfill.
    op.add_column(
        'leads',
        sa.Column('recovery_reason', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('leads', 'recovery_reason')
    op.drop_column('leads', 'entry_channel')
