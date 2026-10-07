import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PriceEvent(UUIDPkMixin, TimestampMixin, Base):
    """One price stated during a conversation, or set on a booking.

    Before this table, get_pricing/negotiate_rate prices lived only in
    in-call ConversationState (NegotiationEvent), which is discarded at
    hangup. Nothing durable recorded what Mira quoted. Append-only: a row is
    never updated except to link booking_id once reconciliation matches the
    conversation to a booking.

    price_type:
      initial_quote  -- get_pricing's quoted total.
      counter_offer  -- negotiate_rate's counter that did not accept the
                        guest's offer.
      accepted_offer -- negotiate_rate accepted the guest's offer.
      final_price    -- a booking's final_booking_price was set (by the host
                        or by reconciliation).
    source: mira_conversation | host_confirmed | host_entered | external_pms.

    price and list_price are stay TOTALS for `nights` nights. list_price is
    the undiscounted asking price that the offer was negotiated from (only
    on negotiation rows), so discount analytics read one row instead of
    re-pairing quotes with offers.
    """

    __tablename__ = "price_events"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL"), index=True
    )
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"), index=True
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), index=True
    )
    price_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    list_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    guest_offer: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    nights: Mapped[int | None] = mapped_column(Integer)
    # Null for a vague-timeline (nights-only) quote -- such a row can never
    # be matched to a booking's exact dates (see booking_reconciliation_service).
    check_in: Mapped[date | None] = mapped_column(Date)
    check_out: Mapped[date | None] = mapped_column(Date)
