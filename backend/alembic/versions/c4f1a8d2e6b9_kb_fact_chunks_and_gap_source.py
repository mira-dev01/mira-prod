"""knowledge-base fact chunks (property_chunks) + unanswered_questions.source

Revision ID: c4f1a8d2e6b9
Revises: bcde09807b54
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4f1a8d2e6b9'
down_revision: Union[str, None] = 'bcde09807b54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Purely additive, no data backfill: every new column is nullable (or has
    # a server default), so existing rows and the running app are unaffected.
    # Fact rows are built lazily on a property's first search_faq, and by
    # scripts/backfill_property_facts.py -- never inside a migration (that
    # would mean embedding API calls during deploy).
    op.add_column('property_chunks', sa.Column('source_kind', sa.String(length=24), nullable=True))
    op.add_column('property_chunks', sa.Column('source_ref', sa.String(length=64), nullable=True))
    op.add_column('property_chunks', sa.Column('meta', postgresql.JSONB(), nullable=True))
    op.create_index('ix_property_chunks_property_type', 'property_chunks', ['property_id', 'chunk_type'])

    op.add_column(
        'unanswered_questions',
        sa.Column('source', sa.String(length=24), nullable=False, server_default='search_miss'),
    )


def downgrade() -> None:
    op.drop_column('unanswered_questions', 'source')
    op.drop_index('ix_property_chunks_property_type', table_name='property_chunks')
    op.drop_column('property_chunks', 'meta')
    op.drop_column('property_chunks', 'source_ref')
    op.drop_column('property_chunks', 'source_kind')
