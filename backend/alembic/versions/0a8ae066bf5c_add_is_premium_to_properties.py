"""add is_premium to properties

Revision ID: 0a8ae066bf5c
Revises: a1c9f4e2b6d3
Create Date: 2026-08-05 14:33:01.856619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0a8ae066bf5c'
down_revision: Union[str, None] = 'a1c9f4e2b6d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the refresh_tokens table, an
    # ix_host_discount_rules_host_id index, and a saturday_minimum_stay_enabled
    # column -- pre-existing drift unrelated to this migration (same pattern
    # already seen in 8818413a6d0a_add_exact_airbnb_pricing_to_properties.py),
    # left untouched here.
    op.add_column('properties', sa.Column('is_premium', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('properties', 'is_premium')
