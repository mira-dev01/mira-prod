"""add agent_language_policy to users

Revision ID: a1c4e8f7b2d3
Revises: d65ddc51db7f
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c4e8f7b2d3'
down_revision: Union[str, None] = 'd65ddc51db7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 3.3 (documentation/agent-conversation-improvement.md): nullable,
    # no server_default -- None/unset means today's unchanged adaptive-
    # mirroring behavior for every existing host, no migration-time backfill
    # needed or wanted.
    op.add_column('users', sa.Column('agent_language_policy', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'agent_language_policy')
