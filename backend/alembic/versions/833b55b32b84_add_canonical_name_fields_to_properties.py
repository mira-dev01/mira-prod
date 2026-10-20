"""add canonical name fields to properties

Revision ID: 833b55b32b84
Revises: 5e62da6e4f7d
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '833b55b32b84'
down_revision: Union[str, None] = '5e62da6e4f7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('raw_name', sa.String(length=255), nullable=True))
    op.add_column('properties', sa.Column('display_name', sa.String(length=120), nullable=True))
    op.add_column('properties', sa.Column('spoken_name', sa.String(length=60), nullable=True))
    op.add_column('properties', sa.Column('property_type', sa.String(length=60), nullable=True))
    op.add_column('properties', sa.Column('property_style', sa.String(length=80), nullable=True))
    op.add_column('properties', sa.Column('brand', sa.String(length=80), nullable=True))
    op.execute("UPDATE properties SET raw_name = name WHERE raw_name IS NULL")


def downgrade() -> None:
    op.drop_column('properties', 'brand')
    op.drop_column('properties', 'property_style')
    op.drop_column('properties', 'property_type')
    op.drop_column('properties', 'spoken_name')
    op.drop_column('properties', 'display_name')
    op.drop_column('properties', 'raw_name')
