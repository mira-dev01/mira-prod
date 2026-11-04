"""faq_entries.group_id: one logical FAQ applied to several properties

Revision ID: d7a2b5c9e1f3
Revises: c4f1a8d2e6b9
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd7a2b5c9e1f3'
down_revision: Union[str, None] = 'c4f1a8d2e6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill: every existing row is a standalone FAQ (None).
    # Rows sharing a group_id are one answer the host applied to several
    # properties; each property still gets its own row so retrieval and
    # fact-syncing stay per-property.
    op.add_column('faq_entries', sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index('ix_faq_entries_group_id', 'faq_entries', ['group_id'])


def downgrade() -> None:
    op.drop_index('ix_faq_entries_group_id', table_name='faq_entries')
    op.drop_column('faq_entries', 'group_id')
