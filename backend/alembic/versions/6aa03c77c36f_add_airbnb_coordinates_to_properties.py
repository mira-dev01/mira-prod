"""add airbnb coordinates to properties

Revision ID: 6aa03c77c36f
Revises: b3f6a1d8c9e2
Create Date: 2026-07-19 22:39:09.962405

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6aa03c77c36f'
down_revision: Union[str, None] = 'b3f6a1d8c9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('airbnb_latitude', sa.Numeric(9, 6), nullable=True))
    op.add_column('properties', sa.Column('airbnb_longitude', sa.Numeric(9, 6), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'airbnb_longitude')
    op.drop_column('properties', 'airbnb_latitude')
