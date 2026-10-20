"""add agent_voice_gender to users

Revision ID: cc04e38bed6f
Revises: b2d7f5a1e3c9
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc04e38bed6f'
down_revision: Union[str, None] = 'b2d7f5a1e3c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('agent_voice_gender', sa.String(length=16), nullable=False, server_default='female'),
    )


def downgrade() -> None:
    op.drop_column('users', 'agent_voice_gender')
