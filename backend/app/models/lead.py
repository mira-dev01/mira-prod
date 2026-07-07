import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Lead(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"), unique=True
    )

    guest_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    check_in: Mapped[date | None] = mapped_column(Date)
    check_out: Mapped[date | None] = mapped_column(Date)
    num_guests: Mapped[int | None] = mapped_column()
    purpose_of_stay: Mapped[str | None] = mapped_column(String(255))
    budget: Mapped[float | None] = mapped_column(Numeric(10, 2))
    preferred_location: Mapped[str | None] = mapped_column(String(255))

    properties_discussed: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    questions_asked: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    support_requests: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    lead_temperature: Mapped[str | None] = mapped_column(String(16))
    lead_source: Mapped[str] = mapped_column(String(64), default="voice_call", server_default="voice_call")
    conversation_summary: Mapped[str | None] = mapped_column(Text)
    next_follow_up: Mapped[str | None] = mapped_column(String(255))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    transferred_to_host: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Lifecycle status for the "Open Leads" overview card -- distinct from
    # lead_temperature (hot/warm/cold), which describes qualification, not
    # whether the host has actually followed up. Host-managed from the
    # Leads page; the voice agent never sets this.
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")

    # Free text, not an enum: guest phrasing for an occasion (birthday,
    # anniversary, honeymoon, ...) varies too much to bucket cleanly, and the
    # point is to capture verbatim what the guest said, not classify it.
    occasion: Mapped[str | None] = mapped_column(String(255))

    owner: Mapped["User"] = relationship(back_populates="leads")
    call_session: Mapped["CallSession"] = relationship(back_populates="lead")
