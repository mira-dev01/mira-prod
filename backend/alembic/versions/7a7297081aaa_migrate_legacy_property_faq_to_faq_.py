"""migrate legacy property.faq to faq_entries

Revision ID: 7a7297081aaa
Revises: c09a22f820ff
Create Date: 2026-07-23 12:30:00.000000

Backfills every non-empty Property.faq JSON entry into a real, verified
FaqEntry row scoped to that property, so the dashboard's per-property FAQ
form (which now writes to FaqEntry instead of the legacy column) doesn't
silently drop what hosts already entered. Property.faq itself is left in
place (still read by app/services/faq_service.search_legacy_property_faq
as a fallback) -- this migration only copies forward, it doesn't clear it.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7a7297081aaa'
down_revision: Union[str, None] = 'c09a22f820ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    properties = connection.execute(
        sa.text('SELECT id, user_id, faq FROM properties WHERE faq IS NOT NULL AND faq != \'[]\'::jsonb')
    ).fetchall()

    for property_id, user_id, faq in properties:
        for item in faq:
            question = (item or {}).get("question")
            answer = (item or {}).get("answer")
            if not question or not answer:
                continue
            connection.execute(
                sa.text(
                    """
                    INSERT INTO faq_entries
                        (id, created_at, updated_at, user_id, property_id, question, answer, status, verified_by)
                    VALUES
                        (:id, now(), now(), :user_id, :property_id, :question, :answer,
                         'verified', 'legacy_property_faq_migration')
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "property_id": property_id,
                    "question": question,
                    "answer": answer,
                },
            )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM faq_entries WHERE verified_by = 'legacy_property_faq_migration'")
    )
