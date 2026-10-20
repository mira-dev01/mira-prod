"""add seasonal_notes to properties

Revision ID: f3a8c1d7e4b6
Revises: e91a3f5c8d2b
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3a8c1d7e4b6'
down_revision: Union[str, None] = 'e91a3f5c8d2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'properties', sa.Column('seasonal_notes', postgresql.JSONB(), nullable=False, server_default='[]')
    )


def downgrade() -> None:
    op.drop_column('properties', 'seasonal_notes')
