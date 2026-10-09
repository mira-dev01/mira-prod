import builtins
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Booking(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("property_id", "source_uid", name="uq_booking_property_source_uid"),)

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    guest_phone: Mapped[str | None] = mapped_column(String(32))
    # Airbnb's iCal DESCRIPTION carries "Phone Number (Last 4 Digits): 1234"
    # for real reservations -- the only guest-identity signal an Airbnb feed
    # exposes. Kept separate from guest_phone (a full number) so nothing that
    # matches on a full phone (guest_booking_service) ever compares against a
    # 4-digit fragment.
    guest_phone_last4: Mapped[str | None] = mapped_column(String(4))
    guest_name: Mapped[str | None] = mapped_column(String(255))
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="airbnb", server_default="airbnb")
    source_uid: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="confirmed", server_default="confirmed")
    # "reservation" (a guest stay) | "blocked" (dates closed with no guest --
    # Airbnb's "Airbnb (Not available)" iCal events, owner holds). Set once
    # from the iCal SUMMARY at sync (booking_reconciliation_service.
    # classify_ical_kind), host-correctable from the Calendar. Both block
    # availability identically (calendar_service only reads status); only
    # analytics distinguishes them -- a block is neither a booked night nor
    # a booking with a price to confirm.
    kind: Mapped[str] = mapped_column(String(16), default="reservation", server_default="reservation")

    # --- Pricing (see app/services/booking_reconciliation_service.py) ---
    # Three distinct prices, never collapsed into one field. All are stay
    # TOTALS (not per-night), in `currency`:
    #   initial_price       -- what Mira first quoted in the matched
    #                          conversation (get_pricing / negotiate_rate's
    #                          asking price). Informational only.
    #   negotiated_price    -- the last price Mira offered/accepted during
    #                          negotiation in the matched conversation.
    #                          Informational only.
    #   final_booking_price -- what the booking was actually confirmed for.
    #                          The ONLY price financial analytics (Revenue/
    #                          ADR/RevPAR/Upcoming Revenue) read. NULL means
    #                          the booking's financial data is incomplete,
    #                          never "assume 0" or "assume the quote".
    initial_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    negotiated_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    final_booking_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    # Where final_booking_price came from: mira_conversation | host_confirmed
    # | host_entered | external_pms | unknown. Plain string validated at the
    # API boundary (schemas/booking.py), same convention as Lead.status.
    price_source: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown")
    # confirmed | pending_confirmation | unknown. "unknown" is every booking
    # that predates this feature; new bookings start pending_confirmation
    # unless reconciliation confidently identifies the final price.
    price_status: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown")
    price_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Actor, not a user id: "host" (the property's owner -- bookings belong
    # to exactly one host account) or "mira" (reconciliation matched an
    # accepted offer to this guest and these exact dates).
    price_confirmed_by: Mapped[str | None] = mapped_column(String(16))
    # When the host last answered the price question for this booking
    # (including "not confirmed yet") -- the reconciliation queue only asks
    # about bookings the host hasn't reviewed, so a deferred answer isn't
    # asked again. Completeness metrics still count it as unpriced.
    price_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Mira attribution (see app/services/booking_reconciliation_service.py) ---
    # confirmed | probable | not_attributed | unknown. Only "confirmed" (a
    # host decision) ever counts toward Mira-attributed bookings/revenue.
    mira_attribution_status: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    mira_attribution_call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL")
    )
    mira_attribution_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), index=True
    )
    # The matching signals behind a probable/not_attributed verdict (e.g.
    # ["same_property", "exact_dates", "phone_last4"]) -- shown to the host
    # next to the Yes/No so the decision is explainable.
    mira_attribution_signals: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    mira_attribution_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped["Property"] = relationship(back_populates="bookings")

    # Derived predicates -- the single definition the API, the
    # reconciliation queue and analytics all use. builtins.property because
    # the `property` relationship above shadows the builtin in this class body.
    @builtins.property
    def is_reservation(self) -> bool:
        return self.kind == "reservation" and self.status == "confirmed"

    @builtins.property
    def has_confirmed_price(self) -> bool:
        return self.final_booking_price is not None and self.price_status == "confirmed"

    @builtins.property
    def needs_price_confirmation(self) -> bool:
        return self.is_reservation and self.final_booking_price is None and self.price_reviewed_at is None

    @builtins.property
    def needs_attribution_review(self) -> bool:
        return (
            self.is_reservation
            and self.mira_attribution_status == "probable"
            and self.mira_attribution_reviewed_at is None
        )

    @builtins.property
    def detected_price(self) -> float | None:
        """The price Mira's matched conversation suggests (the last
        negotiated price, else the first quote) -- offered to the host as a
        one-click answer. None unless a probable/confirmed match exists."""
        if self.mira_attribution_status not in ("probable", "confirmed"):
            return None
        value = self.negotiated_price if self.negotiated_price is not None else self.initial_price
        return float(value) if value is not None else None
