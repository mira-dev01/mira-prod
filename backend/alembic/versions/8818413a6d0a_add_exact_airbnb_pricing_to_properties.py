"""add exact_airbnb_pricing to properties

Revision ID: 8818413a6d0a
Revises: baf955ef4370
Create Date: 2026-07-17 19:30:02.211329

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8818413a6d0a'
down_revision: Union[str, None] = 'baf955ef4370'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping ix_host_discount_rules_host_id --
    # pre-existing drift unrelated to this migration, left untouched here.
    op.add_column('properties', sa.Column('exact_airbnb_pricing', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('properties', 'exact_airbnb_pricing')
