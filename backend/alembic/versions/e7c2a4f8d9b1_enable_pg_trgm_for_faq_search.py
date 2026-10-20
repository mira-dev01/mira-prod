"""enable pg_trgm for fuzzy faq search

Revision ID: e7c2a4f8d9b1
Revises: d3a9f5c1b2e4
Create Date: 2026-07-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7c2a4f8d9b1'
down_revision: Union[str, None] = 'd3a9f5c1b2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
