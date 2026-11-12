"""properties.booking_counter: the running booking number inside a Spacez stay code

Revision ID: a9d4c7e2b6f1
Revises: f2c6b8a4d1e7
Create Date: 2026-11-11 00:00:00.000000

Additive. Every property starts at 0; the first paid booking becomes number 1 (stay code like DECC-0001).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9d4c7e2b6f1'
down_revision: Union[str, None] = 'f2c6b8a4d1e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('booking_counter', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('properties', 'booking_counter')
