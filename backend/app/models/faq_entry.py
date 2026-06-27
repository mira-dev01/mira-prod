import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
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

    owner: Mapped["User"] = relationship(back_populates="faq_entries")
    property: Mapped["Property"] = relationship()
