"""policies + policy_documents: booking policies the agent answers from

Revision ID: c7f2a9d4b8e1
Revises: b6e1d3f8a2c5
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7f2a9d4b8e1'
down_revision: Union[str, None] = 'b6e1d3f8a2c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'policy_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=True),
        sa.Column('size_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('item_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('truncated', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_policy_documents_user_id', 'policy_documents', ['user_id'])

    op.create_table(
        'policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('property_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_document_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('topic', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='active', nullable=False),
        sa.Column('source_excerpt', sa.Text(), nullable=True),
        sa.Column('review_notes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_document_id'], ['policy_documents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_policies_user_status', 'policies', ['user_id', 'status'])
    op.create_index(
        'ix_policies_portfolio_topic', 'policies', ['user_id', 'topic'], unique=True,
        postgresql_where=sa.text("property_id IS NULL AND status = 'active' AND topic <> 'other'"),
    )
    op.create_index(
        'ix_policies_property_topic', 'policies', ['property_id', 'topic'], unique=True,
        postgresql_where=sa.text("property_id IS NOT NULL AND status = 'active' AND topic <> 'other'"),
    )


def downgrade() -> None:
    op.drop_index('ix_policies_property_topic', table_name='policies')
    op.drop_index('ix_policies_portfolio_topic', table_name='policies')
    op.drop_index('ix_policies_user_status', table_name='policies')
    op.drop_table('policies')
    op.drop_index('ix_policy_documents_user_id', table_name='policy_documents')
    op.drop_table('policy_documents')
