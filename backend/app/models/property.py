import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Property(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "properties"
    __table_args__ = (UniqueConstraint("user_id", "airbnb_listing_id", name="uq_properties_user_airbnb_listing"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    exophone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    ical_url: Mapped[str | None] = mapped_column(String(1024))

    # One-line distinguishing description, e.g. "Glass house, 1BHK with a
    # private jacuzzi" -- the system prompt leads with this whenever a guest
    # asks generally about the property, and recommend_properties surfaces it
    # when comparing across a host's portfolio.
    usp: Mapped[str | None] = mapped_column(String(280))

    house_rules: Mapped[str | None] = mapped_column(Text)

    # Free text covering local-area questions guests commonly ask: nearby
    # cafes/restaurants, scooter/bike rental spots, distance to the
    # beach/landmarks, distance to the airport and railway station, cab
    # availability and typical fares, etc. The agent treats this as
    # authoritative for those questions (see app/prompts/system_prompt.py),
    # same as house_rules.
    neighborhood_info: Mapped[str | None] = mapped_column(Text)

    faq: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    amenities: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    # Cloudinary-hosted URLs (see app/integrations/cloudinary_client.py) --
    # re-hosted rather than storing Airbnb's own a0.muscache.com links
    # directly, so they survive the source listing being edited/removed.
    # Populated during Bright Data import; sent to guests who ask to see the
    # property (guest-facing send flow built separately from this column).
    photos: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    check_in_time: Mapped[str] = mapped_column(String(8), default="14:00", server_default="14:00")
    check_out_time: Mapped[str] = mapped_column(String(8), default="11:00", server_default="11:00")
    max_guests: Mapped[int] = mapped_column(default=4, server_default="4")

    # Property Memory (memory-architecture-plan.md section 5) -- the one
    # genuinely new piece beyond consolidating existing fields (house_rules/
    # neighborhood_info/amenities/faq already cover everything else).
    # Time-varying property facts nothing else models: "pool closed in
    # monsoon," "extra heater provided Nov-Feb." Each entry:
    # {note: str, start_month: int (1-12), end_month: int (1-12)}.
    # start_month > end_month is a valid wraparound range (e.g. Nov-Feb =
    # 11-2) -- see system_prompt.py's _active_seasonal_notes for how that's
    # evaluated. Surfaced in the system prompt only when the call's current
    # date falls within a note's range, never unconditionally.
    seasonal_notes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    # Unique per-host (see __table_args__), not globally -- different hosts'
    # own copies of their listing data shouldn't collide with each other,
    # and dev/test data under multiple accounts shouldn't either.
    airbnb_listing_id: Mapped[str | None] = mapped_column(String(64), index=True)

    owner: Mapped["User"] = relationship(back_populates="properties")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    call_sessions: Mapped[list["CallSession"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    technicians: Mapped[list["Technician"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    pricing_rules: Mapped[list["PricingRule"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
