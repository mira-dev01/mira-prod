"""add landmarks and amenity_tags to properties

Revision ID: d16066a213c6
Revises: 833b55b32b84
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd16066a213c6'
down_revision: Union[str, None] = '833b55b32b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('landmarks', postgresql.JSONB(), nullable=False, server_default='[]'))
    op.add_column('properties', sa.Column('amenity_tags', postgresql.JSONB(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('properties', 'amenity_tags')
    op.drop_column('properties', 'landmarks')
