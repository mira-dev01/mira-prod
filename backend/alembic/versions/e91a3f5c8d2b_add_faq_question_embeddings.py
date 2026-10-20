"""add question_embedding to faq_entries and unanswered_questions

Revision ID: e91a3f5c8d2b
Revises: d4f7a91c3e5b
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e91a3f5c8d2b'
down_revision: Union[str, None] = 'd4f7a91c3e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('faq_entries', sa.Column('question_embedding', postgresql.JSONB(), nullable=True))
    op.add_column('unanswered_questions', sa.Column('question_embedding', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('unanswered_questions', 'question_embedding')
    op.drop_column('faq_entries', 'question_embedding')
