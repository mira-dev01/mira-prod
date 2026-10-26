"""merge interests and notification_preferences heads

Revision ID: bcde09807b54
Revises: a7d2e9c4b1f6, b65e5f8bd7c9
Create Date: 2026-10-26 23:21:54.188030

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bcde09807b54'
down_revision: Union[str, None] = ('a7d2e9c4b1f6', 'b65e5f8bd7c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
