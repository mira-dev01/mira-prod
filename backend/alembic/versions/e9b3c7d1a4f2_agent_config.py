"""users.agent_config and properties.agent_config_overrides

Revision ID: e9b3c7d1a4f2
Revises: d7a2b5c9e1f3
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e9b3c7d1a4f2'
down_revision: Union[str, None] = 'd7a2b5c9e1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill: NULL means "every default", which is exactly how
    # every existing host behaves today.
    op.add_column('users', sa.Column('agent_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('properties', sa.Column('agent_config_overrides', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'agent_config_overrides')
    op.drop_column('users', 'agent_config')
