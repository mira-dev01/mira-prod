"""add call_type classification to call_sessions

Revision ID: b3f6a1d8c9e2
Revises: 8818413a6d0a
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f6a1d8c9e2'
down_revision: Union[str, None] = '8818413a6d0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('call_sessions', sa.Column('call_type', sa.String(length=32), server_default='UNKNOWN', nullable=False))
    op.add_column('call_sessions', sa.Column('classification_confidence', sa.Numeric(4, 3), nullable=True))
    op.add_column('call_sessions', sa.Column('classification_reason', sa.Text(), nullable=True))
    op.create_index('ix_call_sessions_call_type', 'call_sessions', ['call_type'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_call_sessions_call_type', table_name='call_sessions')
    op.drop_column('call_sessions', 'classification_reason')
    op.drop_column('call_sessions', 'classification_confidence')
    op.drop_column('call_sessions', 'call_type')
