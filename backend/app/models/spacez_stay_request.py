"""SPACEZ ONLY. A guest with a confirmed booking asked, on a call, for an early check-in and/or a late check-out.

The team (not Mira) decides yes or no -- the villa has to be free -- so the request waits on a ticket
(kind "stay_request"). Approve: a payment link for the flat fee goes to the guest on WhatsApp. Decline:
the guest is told on WhatsApp the team cannot do it. All the logic is in app/services/spacez_stay_requests.py.

    pending ──► awaiting_payment ──► paid
       │               └──────────► expired     (link lapsed or was cancelled unpaid)
       └──────► declined
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class SpacezStayRequest(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "spacez_stay_requests"
    __table_args__ = (
        Index("ix_spacez_stay_requests_user_status", "user_id", "status"),
        Index("ix_spacez_stay_requests_ticket", "ticket_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"))
    # The guest's paid booking this is an add-on to.
    payment_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_requests.id", ondelete="SET NULL"))

    guest_name: Mapped[str | None] = mapped_column(String(255))
    guest_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)

    want_early_checkin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    want_late_checkout: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    note: Mapped[str | None] = mapped_column(Text)  # e.g. "around 10 am" in the guest's words
    early_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    late_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")

    # pending | awaiting_payment | paid | declined | expired
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    razorpay_link_id: Mapped[str | None] = mapped_column(String(64))
    short_url: Mapped[str | None] = mapped_column(String(255))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
