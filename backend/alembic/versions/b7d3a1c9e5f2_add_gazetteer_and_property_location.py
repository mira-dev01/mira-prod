"""gazetteer tables + property latitude/longitude/state/district

Revision ID: b7d3a1c9e5f2
Revises: f4b8d2a6c1e9
Create Date: 2026-11-07 00:00:00.000000

Additive only. The new property columns are nullable and nothing reads them
until a property has a point, so every existing property behaves exactly as
before. The gazetteer tables are created empty here; scripts/load_gazetteer.py
fills them (until it runs, place lookups simply find nothing and the old
free-text location match is used on its own).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d3a1c9e5f2'
down_revision: Union[str, None] = 'f4b8d2a6c1e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'admin_regions',
        sa.Column('code', sa.String(24), primary_key=True),
        sa.Column('level', sa.SmallInteger(), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
    )
    op.create_table(
        'places',
        sa.Column('geoname_id', sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('name_norm', sa.String(120), nullable=False),
        sa.Column('name_key', sa.String(40), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('admin1_code', sa.String(8), nullable=True),
        sa.Column('admin2_code', sa.String(24), nullable=True),
        sa.Column('population', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_index('ix_places_name_norm', 'places', ['name_norm'])
    op.create_index('ix_places_name_key', 'places', ['name_key'])
    op.create_index('ix_places_lat_lng', 'places', ['lat', 'lng'])
    op.create_table(
        'place_aliases',
        sa.Column('place_id', sa.BigInteger(), sa.ForeignKey('places.geoname_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('alias_norm', sa.String(120), primary_key=True),
        sa.Column('alias_key', sa.String(40), nullable=False),
    )
    op.create_index('ix_place_aliases_alias_norm', 'place_aliases', ['alias_norm'])
    op.create_index('ix_place_aliases_alias_key', 'place_aliases', ['alias_key'])

    op.add_column('properties', sa.Column('latitude', sa.Numeric(9, 6), nullable=True))
    op.add_column('properties', sa.Column('longitude', sa.Numeric(9, 6), nullable=True))
    op.add_column('properties', sa.Column('state', sa.String(80), nullable=True))
    op.add_column('properties', sa.Column('district', sa.String(80), nullable=True))
    op.add_column('properties', sa.Column('location_source', sa.String(24), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'location_source')
    op.drop_column('properties', 'district')
    op.drop_column('properties', 'state')
    op.drop_column('properties', 'longitude')
    op.drop_column('properties', 'latitude')
    op.drop_table('place_aliases')
    op.drop_table('places')
    op.drop_table('admin_regions')
