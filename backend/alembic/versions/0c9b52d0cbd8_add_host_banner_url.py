"""add host banner_url

Revision ID: 0c9b52d0cbd8
Revises: fb704d3f696c
Create Date: 2026-07-26 01:12:06.785947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0c9b52d0cbd8'
down_revision: Union[str, None] = 'fb704d3f696c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the 'refresh_tokens' table,
    # a call_sessions.ai_summary type change, dropping
    # call_sessions.dismissed_at, dropping ix_host_discount_rules_host_id,
    # and dropping users.agent_voice_gender -- all pre-existing drift
    # unrelated to this migration, left untouched here.
    op.add_column('users', sa.Column('banner_url', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'banner_url')
