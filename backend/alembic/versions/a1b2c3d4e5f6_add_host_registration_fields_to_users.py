"""add host registration fields to users

Revision ID: a1b2c3d4e5f6
Revises: e7c2a4f8d9b1
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e7c2a4f8d9b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('business_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('airbnb_host_status', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('property_count_estimate', sa.Integer(), nullable=True))
    op.add_column(
        'users',
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='Asia/Kolkata'),
    )
    op.add_column('users', sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'terms_accepted_at')
    op.drop_column('users', 'timezone')
    op.drop_column('users', 'property_count_estimate')
    op.drop_column('users', 'airbnb_host_status')
    op.drop_column('users', 'business_name')
