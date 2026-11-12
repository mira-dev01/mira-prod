import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Ticket(UUIDPkMixin, TimestampMixin, Base):
    """Something a guest was told would happen after the call, kept open until
    the host does it. Notifications are alerts that scroll away; a ticket is
    the durable obligation behind them -- every "I've flagged this to the
    host", "the host will follow up" or "someone will fix it" the agent says
    has one (see app/services/ticket_service.py).
    """

    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_user_status", "user_id", "status"),
        Index("ix_tickets_call_session", "call_session_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL")
    )
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL")
    )
    # The alert that was raised alongside, when there is one.
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL")
    )

    # escalation | transfer | technician | photos | whatsapp | promise | missed_call | payment
    # | stay_request (Spacez only: early check-in / late check-out awaiting the team's yes or no)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # What the host has to do, in one line.
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    # low | medium | high | emergency (same vocabulary as Notification.urgency)
    urgency: Mapped[str] = mapped_column(String(16), default="medium", server_default="medium")
    # open | done | dismissed
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    # tool = opened by a tool as it ran; post_call = the end-of-call check
    # found a promise nothing had recorded.
    source: Mapped[str] = mapped_column(String(16), default="tool", server_default="tool")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
