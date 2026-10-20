"""add smart pricing fields to properties

Revision ID: baf955ef4370
Revises: f3a8c1d7e4b6
Create Date: 2026-07-15 19:35:54.448383

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'baf955ef4370'
down_revision: Union[str, None] = 'f3a8c1d7e4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping ix_host_discount_rules_host_id --
    # pre-existing drift unrelated to this migration, left untouched here.
    op.add_column('properties', sa.Column('smart_price_estimate', sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column('properties', sa.Column('smart_price_sample_size', sa.Integer(), server_default='0', nullable=False))
    op.add_column('properties', sa.Column('smart_price_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('properties', 'smart_price_updated_at')
    op.drop_column('properties', 'smart_price_sample_size')
    op.drop_column('properties', 'smart_price_estimate')
