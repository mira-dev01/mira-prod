"""users.agent_customized_at: when the host last changed something the agent says or does

Revision ID: d1e7b3a9c4f2
Revises: c7f1a3d9e5b2
Create Date: 2026-11-22 00:00:00.000000

Additive and nullable: NULL means "no change since this existed", so nobody sees the
"Test your agent" highlight until they change something. Written only by
app/services/agent_change_tracker.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e7b3a9c4f2'
down_revision: Union[str, None] = 'c7f1a3d9e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('agent_customized_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'agent_customized_at')
