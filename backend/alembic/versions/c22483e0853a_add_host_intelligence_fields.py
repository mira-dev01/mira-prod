"""add host intelligence fields

Revision ID: c22483e0853a
Revises: 66f90a703525
Create Date: 2026-08-05 23:20:03.156799

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c22483e0853a'
down_revision: Union[str, None] = '66f90a703525'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the refresh_tokens table, an
    # ix_host_discount_rules_host_id index, and a saturday_minimum_stay_enabled
    # column -- pre-existing drift unrelated to this migration (same pattern
    # already seen in every prior migration this session, e.g.
    # 0a8ae066bf5c_add_is_premium_to_properties.py), left untouched here.
    op.add_column('properties', sa.Column('is_priority', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('host_notes', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('host_instructions', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('escalation_urgency_bias', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('suggest_upsells_proactively', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'suggest_upsells_proactively')
    op.drop_column('users', 'escalation_urgency_bias')
    op.drop_column('users', 'host_instructions')
    op.drop_column('users', 'host_notes')
    op.drop_column('properties', 'is_priority')
