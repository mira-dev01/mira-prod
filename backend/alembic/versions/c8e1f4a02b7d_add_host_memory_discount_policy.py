"""add host memory discount policy fields and host_discount_rules table

Revision ID: c8e1f4a02b7d
Revises: b7d4e6f2a913
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c8e1f4a02b7d'
down_revision: Union[str, None] = 'b7d4e6f2a913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('discount_policy_text', sa.Text(), nullable=True))
    op.add_column(
        'users',
        sa.Column('negotiation_allowed', sa.Boolean(), nullable=False, server_default='true'),
    )
    op.add_column('users', sa.Column('max_discount_percent_override', sa.Numeric(5, 2), nullable=True))
    op.add_column('users', sa.Column('allow_pets', sa.Boolean(), nullable=True))
    op.add_column('users', sa.Column('allow_early_checkin', sa.Boolean(), nullable=True))
    op.add_column('users', sa.Column('follow_up_channel_preference', sa.String(length=32), nullable=True))

    op.create_table(
        'host_discount_rules',
        sa.Column('host_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('trigger_type', sa.String(length=64), nullable=False),
        sa.Column('discount_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='ai_parsed'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending_validation'),
        sa.Column('raw_source_text', sa.Text(), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_host_discount_rules_host_id', 'host_discount_rules', ['host_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_host_discount_rules_host_id', table_name='host_discount_rules')
    op.drop_table('host_discount_rules')
    op.drop_column('users', 'follow_up_channel_preference')
    op.drop_column('users', 'allow_early_checkin')
    op.drop_column('users', 'allow_pets')
    op.drop_column('users', 'max_discount_percent_override')
    op.drop_column('users', 'negotiation_allowed')
    op.drop_column('users', 'discount_policy_text')
