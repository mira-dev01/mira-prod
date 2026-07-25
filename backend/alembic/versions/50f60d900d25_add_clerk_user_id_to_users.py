"""add clerk_user_id to users

Revision ID: 50f60d900d25
Revises: 5161e38a221b
Create Date: 2026-07-24 22:44:08.092285

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '50f60d900d25'
down_revision: Union[str, None] = '5161e38a221b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the 'refresh_tokens' table
    # and ix_host_discount_rules_host_id -- pre-existing drift unrelated to
    # this migration, left untouched here.
    op.add_column('users', sa.Column('clerk_user_id', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_users_clerk_user_id'), 'users', ['clerk_user_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_clerk_user_id'), table_name='users')
    op.drop_column('users', 'clerk_user_id')
