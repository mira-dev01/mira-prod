"""add property_chunks

Revision ID: d65ddc51db7f
Revises: 810e248aba2c
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd65ddc51db7f'
down_revision: Union[str, None] = '810e248aba2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'property_chunks',
        sa.Column('property_id', sa.UUID(), nullable=False),
        sa.Column('chunk_type', sa.String(length=32), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_property_chunks_property_id'), 'property_chunks', ['property_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_property_chunks_property_id'), table_name='property_chunks')
    op.drop_table('property_chunks')
