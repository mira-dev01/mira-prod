"""make hashed_password nullable for Clerk cutover

Revision ID: c09a22f820ff
Revises: 50f60d900d25
Create Date: 2026-07-25 12:35:03.282845

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c09a22f820ff'
down_revision: Union[str, None] = '50f60d900d25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the 'refresh_tokens' table
    # and ix_host_discount_rules_host_id -- pre-existing drift unrelated to
    # this migration, left untouched here.
    op.alter_column('users', 'hashed_password',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)


def downgrade() -> None:
    op.alter_column('users', 'hashed_password',
               existing_type=sa.VARCHAR(length=255),
               nullable=False)
