"""add saturday minimum stay to properties

Revision ID: b7e3a9c1f4d6
Revises: a1c9f4e2b6d3
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7e3a9c1f4d6'
down_revision: Union[str, None] = 'a1c9f4e2b6d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'properties',
        sa.Column('saturday_minimum_stay_enabled', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('properties', 'saturday_minimum_stay_enabled')
