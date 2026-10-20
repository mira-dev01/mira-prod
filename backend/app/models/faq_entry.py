import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class FaqEntry(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "faq_entries"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    verified_by: Mapped[str | None] = mapped_column(String(255))
    # Knowledge Memory (memory-architecture-plan.md section 3.1) -- question
    # embedding (see app/services/embedding_service.py), computed once at
    # verification time and stored as a plain float array (not pgvector --
    # production DB extension availability can't be verified from here, and
    # the comparison set per host is small enough for in-Python cosine
    # similarity). Null for entries created before this migration or if the
    # embedding call failed -- both are treated as "no semantic match
    # possible for this entry," never an error.
    question_embedding: Mapped[list | None] = mapped_column(JSONB)

    owner: Mapped["User"] = relationship(back_populates="faq_entries")
    property: Mapped["Property"] = relationship()
