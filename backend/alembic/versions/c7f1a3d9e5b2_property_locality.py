"""properties.locality and properties.locality_key: the area inside a city

Revision ID: c7f1a3d9e5b2
Revises: b4e8a1c6d2f9
Create Date: 2026-11-14 00:00:00.000000

Additive and nullable: every existing property starts with no locality, and the search falls back to
what it did before for a property that has none. `locality_key` is the matching form of `locality`;
only app/services/property/locality.py writes either.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f1a3d9e5b2'
down_revision: Union[str, None] = 'b4e8a1c6d2f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('locality', sa.String(length=120), nullable=True))
    op.add_column('properties', sa.Column('locality_key', sa.String(length=120), nullable=True))
    op.create_index(op.f('ix_properties_locality_key'), 'properties', ['locality_key'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_properties_locality_key'), table_name='properties')
    op.drop_column('properties', 'locality_key')
    op.drop_column('properties', 'locality')
