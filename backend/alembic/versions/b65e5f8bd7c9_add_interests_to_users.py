"""add interests to users

Revision ID: b65e5f8bd7c9
Revises: d4a7b3e9c2f1
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b65e5f8bd7c9'
down_revision: Union[str, None] = 'd4a7b3e9c2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'interests', postgresql.JSONB(astext_type=sa.Text()),
        server_default='[]', nullable=False,
    ))


def downgrade() -> None:
    op.drop_column('users', 'interests')
