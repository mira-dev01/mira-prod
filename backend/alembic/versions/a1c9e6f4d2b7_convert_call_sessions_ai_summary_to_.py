"""convert call_sessions.ai_summary to jsonb

Revision ID: a1c9e6f4d2b7
Revises: 7a7297081aaa
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1c9e6f4d2b7'
down_revision: Union[str, None] = '7a7297081aaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ai_summary was always NULL in practice (no summarizer existed yet --
    # see app/services/call_summary_service.py), so there's no free-text data
    # to migrate into the new structured shape. USING clause still guards
    # against the unlikely case of a manually-inserted plain-text value.
    op.execute(
        "ALTER TABLE call_sessions "
        "ALTER COLUMN ai_summary TYPE JSONB "
        "USING CASE WHEN ai_summary IS NULL THEN NULL ELSE to_jsonb(ai_summary) END"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE call_sessions "
        "ALTER COLUMN ai_summary TYPE TEXT "
        "USING ai_summary::text"
    )
