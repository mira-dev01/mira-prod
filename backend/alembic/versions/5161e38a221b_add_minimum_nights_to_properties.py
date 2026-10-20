"""add minimum_nights to properties

Revision ID: 5161e38a221b
Revises: d8a1f47c2b6e
Create Date: 2026-07-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5161e38a221b'
down_revision: Union[str, None] = 'd8a1f47c2b6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('minimum_nights', sa.Integer(), server_default='1', nullable=False))


def downgrade() -> None:
    op.drop_column('properties', 'minimum_nights')
