"""call_sessions.escalation_markers: which transcript lines were Mira escalating to the host

Revision ID: e4b8c2f6a1d3
Revises: d1e7b3a9c4f2
Create Date: 2026-11-23 00:00:00.000000

Additive and nullable: NULL (every call before this) means "not recorded" ([] = escalated nothing), and the dashboard falls back to
recognising the escalation wording. Written once, at the end of the call (services/transcript_markers.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e4b8c2f6a1d3'
down_revision: Union[str, None] = 'd1e7b3a9c4f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('call_sessions', sa.Column('escalation_markers', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('call_sessions', 'escalation_markers')
