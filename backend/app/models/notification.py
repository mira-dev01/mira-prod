import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Notification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # whatsapp | escalation | system
    urgency: Mapped[str] = mapped_column(String(16), default="low", server_default="low")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")

    property: Mapped["Property"] = relationship(back_populates="notifications")
    call_session: Mapped["CallSession"] = relationship(back_populates="notifications")
