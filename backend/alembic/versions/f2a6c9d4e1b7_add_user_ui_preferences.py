"""add user_ui_preferences (navigation + overview widget layout)

Revision ID: f2a6c9d4e1b7
Revises: e8c3f1a7b2d9
Create Date: 2026-10-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f2a6c9d4e1b7'
down_revision: Union[str, None] = 'e8c3f1a7b2d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive, no backfill: a user with no row gets the registry's
    # default layout, which is exactly today's sidebar and Overview -- see
    # app/models/user_ui_preference.py.
    op.create_table(
        'user_ui_preferences',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key', sa.String(length=32), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('data', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('user_id', 'key', name='uq_user_ui_preferences_user_key'),
    )
    op.create_index('ix_user_ui_preferences_user_id', 'user_ui_preferences', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_ui_preferences_user_id', table_name='user_ui_preferences')
    op.drop_table('user_ui_preferences')
