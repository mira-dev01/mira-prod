import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class UnansweredQuestion(UUIDPkMixin, TimestampMixin, Base):
    """A guest question search_faq (app/services/tool_handlers.py
    handle_search_faq) had no verified answer for. One row per occurrence --
    frequency is computed at query time via GROUP BY normalized_question
    rather than incrementing a counter, since there's no semantic-similarity
    matching here, just exact-text grouping after trim/lowercase.
    """

    __tablename__ = "unanswered_questions"
    __table_args__ = (
        Index("ix_unanswered_questions_user_normalized_status", "user_id", "normalized_question", "status"),
        Index("ix_unanswered_questions_user_property", "user_id", "property_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL")
    )
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL")
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Trimmed/lowercased copy of `question`, used for GROUP BY frequency
    # counting and to catch all matching rows when a gap gets answered.
    normalized_question: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    resolved_faq_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("faq_entries.id", ondelete="SET NULL")
    )
    # Knowledge Memory (memory-architecture-plan.md section 3.1) -- see
    # FaqEntry.question_embedding for why this is a plain float array, not
    # pgvector. Computed once when the gap is first logged
    # (handle_search_faq); null means no semantic suggestion is possible
    # for this row, never an error.
    question_embedding: Mapped[list | None] = mapped_column(JSONB)
