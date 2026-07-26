"""merge host profile and voice-gender/dismissed-at branches

Revision ID: 5e62da6e4f7d
Revises: 0c9b52d0cbd8, cc04e38bed6f
Create Date: 2026-07-26 12:55:37.846102

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e62da6e4f7d'
down_revision: Union[str, None] = ('0c9b52d0cbd8', 'cc04e38bed6f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
