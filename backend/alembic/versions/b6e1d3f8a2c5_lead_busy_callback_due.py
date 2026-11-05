"""leads.busy_recovery_callback_due_at: when to check that a busy-call guest rang back

Revision ID: b6e1d3f8a2c5
Revises: a5d9e2c7b1f4
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6e1d3f8a2c5'
down_revision: Union[str, None] = 'a5d9e2c7b1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill: NULL means "nothing is waiting on a callback",
    # which is true of every existing lead.
    op.add_column('leads', sa.Column('busy_recovery_callback_due_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_leads_busy_recovery_callback_due_at', 'leads', ['busy_recovery_callback_due_at'])


def downgrade() -> None:
    op.drop_index('ix_leads_busy_recovery_callback_due_at', table_name='leads')
    op.drop_column('leads', 'busy_recovery_callback_due_at')
