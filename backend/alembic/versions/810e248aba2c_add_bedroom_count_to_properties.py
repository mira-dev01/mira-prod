"""add bedroom_count to properties

Revision ID: 810e248aba2c
Revises: d16066a213c6
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '810e248aba2c'
down_revision: Union[str, None] = 'd16066a213c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('bedroom_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'bedroom_count')
