"""add host photo and whatsapp assist toggle

Revision ID: fb704d3f696c
Revises: 7a7297081aaa
Create Date: 2026-07-26 00:55:07.835561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fb704d3f696c'
down_revision: Union[str, None] = '7a7297081aaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the 'refresh_tokens' table,
    # a call_sessions.ai_summary type change, dropping
    # call_sessions.dismissed_at, dropping ix_host_discount_rules_host_id,
    # and dropping users.agent_voice_gender -- all pre-existing drift
    # unrelated to this migration, left untouched here.
    op.add_column('users', sa.Column('photo_url', sa.String(length=512), nullable=True))
    op.add_column('users', sa.Column('whatsapp_assist_enabled', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'whatsapp_assist_enabled')
    op.drop_column('users', 'photo_url')
