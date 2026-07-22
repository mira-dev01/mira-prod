"""add lead_id to call_sessions

Revision ID: d8a1f47c2b6e
Revises: 6aa03c77c36f
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8a1f47c2b6e'
down_revision: Union[str, None] = '6aa03c77c36f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # leads.call_session_id (unique) still means "the call that originally
    # created this lead" -- unchanged. This new column is what lets a LATER
    # call from the same returning guest point at that same, already-
    # existing lead instead of always getting its own new row (see
    # lead_service.py's reuse logic). Many call_sessions can now share one
    # lead_id; leads.call_session_id stays 1:1 with its originating call.
    op.add_column('call_sessions', sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_call_sessions_lead_id_leads', 'call_sessions', 'leads', ['lead_id'], ['id'], ondelete='SET NULL'
    )
    op.create_index('ix_call_sessions_lead_id', 'call_sessions', ['lead_id'], unique=False)

    # Backfill: every existing 1:1 (lead.call_session_id -> call_session.id)
    # pair already has a well-defined lead_id -- populate it so historical
    # calls keep resolving their lead via the same lookup path as new ones.
    op.execute(
        """
        UPDATE call_sessions
        SET lead_id = leads.id
        FROM leads
        WHERE leads.call_session_id = call_sessions.id
        """
    )


def downgrade() -> None:
    op.drop_index('ix_call_sessions_lead_id', table_name='call_sessions')
    op.drop_constraint('fk_call_sessions_lead_id_leads', 'call_sessions', type_='foreignkey')
    op.drop_column('call_sessions', 'lead_id')
