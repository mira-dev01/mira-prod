import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class CallSession(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "call_sessions"

    exotel_call_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL")
    )
    guest_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guest_profiles.id", ondelete="SET NULL")
    )
    caller_number: Mapped[str | None] = mapped_column(String(32))
    recording_url: Mapped[str | None] = mapped_column(String(1024))
    transcript: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="in_progress", server_default="in_progress")
    urgency: Mapped[str | None] = mapped_column(String(16))
    revenue_attributed: Mapped[float] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped["Property"] = relationship(back_populates="call_sessions")
    guest_profile: Mapped["GuestProfile"] = relationship(back_populates="call_sessions")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="call_session")
