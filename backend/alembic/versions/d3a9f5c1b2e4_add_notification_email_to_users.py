"""add notification_email to users

Revision ID: d3a9f5c1b2e4
Revises: f3cebf679a80
Create Date: 2026-07-09 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3a9f5c1b2e4'
down_revision: Union[str, None] = 'f3cebf679a80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('notification_email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'notification_email')
