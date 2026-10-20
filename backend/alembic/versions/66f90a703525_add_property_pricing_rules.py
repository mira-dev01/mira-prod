"""add property pricing rules

Revision ID: 66f90a703525
Revises: 0a8ae066bf5c
Create Date: 2026-08-05 19:17:27.505742

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '66f90a703525'
down_revision: Union[str, None] = '0a8ae066bf5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping the refresh_tokens table, an
    # ix_host_discount_rules_host_id index, and a saturday_minimum_stay_enabled
    # column -- pre-existing drift unrelated to this migration (same pattern
    # already seen in 8818413a6d0a_add_exact_airbnb_pricing_to_properties.py
    # and 0a8ae066bf5c_add_is_premium_to_properties.py), left untouched here.
    op.create_table('property_pricing_rules',
    sa.Column('host_id', sa.UUID(), nullable=False),
    sa.Column('rule_type', sa.String(length=64), nullable=False),
    sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('discount_percent', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('property_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('source', sa.String(length=16), server_default='ai_parsed', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='pending_validation', nullable=False),
    sa.Column('raw_source_text', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['host_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('property_pricing_rules')
