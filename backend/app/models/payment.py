import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PaymentRequest(UUIDPkMixin, TimestampMixin, Base):
    """A payment link sent to a guest, and the hold on the dates that goes with
    it. While `status` is active or paid, a mirror row in `bookings` blocks the
    dates for everyone else (so every availability check already respects it);
    that row is removed if the hold lapses or is cancelled, and becomes the real
    reservation when the guest pays.

    Lifecycle: active -> paid | expired | cancelled. Every transition is a
    single atomic UPDATE ... WHERE status = <from> (see payment_service), so two
    workers can never both act on the same request.
    """

    __tablename__ = "payment_requests"
    __table_args__ = (
        Index("ix_payment_requests_status_expiry", "status", "hold_expires_at"),
        Index("ix_payment_requests_user_status", "user_id", "status"),
        Index("ix_payment_requests_property", "property_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"))
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"))
    # The blocking row in `bookings` for this hold/reservation.
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"))

    guest_name: Mapped[str | None] = mapped_column(String(255))
    guest_phone: Mapped[str | None] = mapped_column(String(32))
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)

    # The stay total Mira quoted, and what this link actually charges (all of
    # it, or the advance when the host splits payment). Rupees.
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    # full | advance
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="full", server_default="full")
    # The host's "who confirms the booking" choice at the time (host | payment).
    confirm_by: Mapped[str] = mapped_column(String(12), nullable=False, default="host", server_default="host")
    balance_due_on: Mapped[date | None] = mapped_column(Date)

    # active | paid | expired | cancelled
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active", server_default="active")
    razorpay_link_id: Mapped[str | None] = mapped_column(String(64))
    short_url: Mapped[str | None] = mapped_column(String(255))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64))
    # The Razorpay link has been cancelled (or can no longer be paid), so a
    # late payment is impossible and polling can stop.
    link_closed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    hold_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    success_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    host_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The WhatsApp message carrying the link, and when we last confirmed what
    # became of it (a freeform message to a guest who has not messaged the
    # business can be accepted by Twilio and then never delivered).
    link_message_sid: Mapped[str | None] = mapped_column(String(64))
    link_delivery_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # What the booking is for, kept with the hold so the confirmation can be written from it: the guests
    # (adults/children/pets), purpose of stay, email, the bill, the stay code once paid. NULL for a plain
    # (flat-price) payment link.
    details: Mapped[dict | None] = mapped_column(JSONB)


class HoldWaiter(UUIDPkMixin, TimestampMixin, Base):
    """A second guest who asked for dates that were on hold for someone else and
    was promised an update if the hold lapses."""

    __tablename__ = "hold_waiters"
    __table_args__ = (UniqueConstraint("payment_request_id", "guest_phone", name="uq_hold_waiter_request_phone"),)

    payment_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    guest_name: Mapped[str | None] = mapped_column(String(255))
    guest_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"))
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
