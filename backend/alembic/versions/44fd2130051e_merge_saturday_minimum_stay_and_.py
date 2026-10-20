"""merge saturday minimum stay and negotiation engine heads

Revision ID: 44fd2130051e
Revises: b7e3a9c1f4d6, 7a236ad1ffd1
Create Date: 2026-08-09 18:53:12.266320

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44fd2130051e'
down_revision: Union[str, None] = ('b7e3a9c1f4d6', '7a236ad1ffd1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
