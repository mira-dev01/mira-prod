import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Property(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "properties"
    __table_args__ = (UniqueConstraint("user_id", "airbnb_listing_id", name="uq_properties_user_airbnb_listing"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # raw_name: verbatim copy of whatever the importer wrote into `name` at
    # import time (Airbnb's own SEO marketing title, unedited) -- `name`
    # itself is kept exactly as-is for backward compat (every existing
    # reader keeps working unchanged); raw_name/display_name/spoken_name
    # below are what new guest-facing and dashboard code should read.
    raw_name: Mapped[str | None] = mapped_column(String(255))

    # display_name: clean, human-presentable name for dashboards/lists --
    # may still include a light descriptor ("Pine Glasshouse Suite"), just
    # stripped of SEO keyword-stuffing, star ratings, and pipe/dot-delimited
    # marketing fragments. See app/services/property_normalizer.py.
    display_name: Mapped[str | None] = mapped_column(String(120))

    # spoken_name: what Mira actually says out loud to a guest -- shorter
    # than display_name, no punctuation that reads awkwardly aloud. Falls
    # back to display_name when nothing shorter is safely extractable.
    # This is what property_recommendation_guard matches spoken text
    # against to confirm a recommended property was actually named.
    spoken_name: Mapped[str | None] = mapped_column(String(60))

    # property_type: coarse category normalized from the raw title/
    # description (villa, cottage, apartment, cabin, glasshouse, etc).
    # Free text, not an enum -- source titles are too varied to constrain
    # to a fixed list without frequently falling back to "other".
    property_type: Mapped[str | None] = mapped_column(String(60))

    # property_style: descriptive style/vibe distinct from type (e.g.
    # "glass house", "beachfront", "forest cabin") -- display/context only,
    # not a filterable facet (property_type/amenities/landmarks are).
    property_style: Mapped[str | None] = mapped_column(String(80))

    # brand: the recurring multi-property brand/collection name a title
    # mentions (e.g. "Pause Project"), when confidently identified via
    # cross-property co-occurrence for the same host. None otherwise.
    brand: Mapped[str | None] = mapped_column(String(80))

    # bedroom_count: extracted from the raw title by property_normalizer
    # (bhk/"N bedroom"/"NBR" style patterns) -- used by the spoken pitch
    # formatter ("a one-bedroom glasshouse suite") instead of parroting a
    # raw "1bhk" fragment. None when the title had no extractable count.
    bedroom_count: Mapped[int | None] = mapped_column()

    city: Mapped[str | None] = mapped_column(String(120))
    exophone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    # Twilio equivalent of exophone above -- a Twilio phone number routed to
    # this property's Guest Support line, independent of and never read by
    # any Exotel code path. Added so telephony testing can continue on
    # Twilio's free trial when Exotel credits run out, without touching the
    # Exotel routing/pipeline at all. See app/api/v1/voice.py's twilio_*
    # routes and app/voice/pipeline.py's run_voice_pipeline_twilio.
    twilio_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
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

    # Structured points of guest interest near this property, e.g.
    # [{"name": "Thalassa", "distance_minutes": 12, "mode": "drive"}].
    # Host-dashboard-populated (no scraper reliably provides this --
    # Bright Data's location_details is free text) -- empty by default.
    # recommend_properties (app/services/tool_handlers.py) uses this for a
    # "near <landmark>" query as a soft rank signal, falling back to a
    # plain neighborhood_info substring match when a property has none yet.
    landmarks: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    faq: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    amenities: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    # Canonical, deduplicated amenity tags derived from `amenities` at
    # import time (see app/services/amenity_taxonomy.py) -- e.g. "Private
    # pool"/"Swimming pool" both normalize to "pool". Kept alongside the
    # free-text `amenities` list (which stays the display source, unchanged)
    # so recommend_properties can filter on a canonical value instead of an
    # inconsistent free-text ILIKE. Recomputed whenever `amenities` is
    # re-imported; not directly host-editable.
    amenity_tags: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    # Cloudinary-hosted URLs (see app/integrations/cloudinary_client.py) --
    # re-hosted rather than storing Airbnb's own a0.muscache.com links
    # directly, so they survive the source listing being edited/removed.
    # Populated during Bright Data import; sent to guests who ask to see the
    # property (guest-facing send flow built separately from this column).
    photos: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    check_in_time: Mapped[str] = mapped_column(String(8), default="14:00", server_default="14:00")
    check_out_time: Mapped[str] = mapped_column(String(8), default="11:00", server_default="11:00")
    max_guests: Mapped[int] = mapped_column(default=4, server_default="4")

    # Airbnb's own minimum-stay setting for this listing -- not enforced
    # anywhere before this column existed, so check_calendar could confirm
    # "available" for a 1-night request even when Airbnb itself requires 2+
    # (confirmed live against a host's actual listing). Default 1 = no
    # constraint, matching every property that predates this field.
    minimum_nights: Mapped[int] = mapped_column(default=1, server_default="1")

    # Weekend/Saturday minimum-stay rule -- independent of minimum_nights
    # above (that one applies to every stay; this one only kicks in when the
    # requested stay includes a Saturday night). When True, a stay that
    # covers a Saturday night must be at least 2 nights -- a lone
    # Saturday-only 1-night booking is rejected, same as many real Airbnb
    # hosts' own "Require Saturday-Sunday" weekend policy. Checked in
    # handle_check_calendar (app/services/tool_handlers.py), same place
    # minimum_nights is enforced, not duplicated into pricing/negotiation --
    # matching that field's existing precedent.
    saturday_minimum_stay_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

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

    # Median nightly rate across comparable live Airbnb listings in this
    # property's city, refreshed daily by app/services/smart_pricing_service.py
    # via SearchApi.io's Airbnb engine (SEARCHAPI_API_KEY). Purely
    # informational -- never fed automatically into pricing_engine's
    # get_pricing/negotiate_rate math, which stays host-rule-driven.
    # None until the first refresh has run for this property's city.
    smart_price_estimate: Mapped[float | None] = mapped_column(Numeric(10, 2))
    smart_price_sample_size: Mapped[int] = mapped_column(default=0, server_default="0")
    smart_price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Whether Mira quotes this property's live Airbnb Smart Pricing (fetched
    # per pricing question via SearchApi, see pricing_engine.calculate_price
    # and airbnb_latitude/longitude below) instead of the host-set
    # base_price. Also gates whether SearchApi is used for this property at
    # all -- both this live per-listing lookup and the city-comparable daily
    # refresh (app/services/smart_pricing_service.py) are scoped to
    # properties with this on, since not every host is on Airbnb Smart
    # Pricing. Defaults false: quotes base_price as-is, no markup either
    # way (pricing_engine.calculate_price applies none, on or off). Length-
    # of-stay discounts still apply either way -- those are a host-
    # configured discount, not a markup.
    exact_airbnb_pricing: Mapped[bool] = mapped_column(default=False, server_default="false")

    # Recommendation conversations ("Phase X"): host-set, never LLM-inferred --
    # same discipline as every other guest-facing fact in this codebase (no
    # LLM-self-reported judgment, per agent-conversation-improvement.md's own
    # Non-goals). Grounds a guest's relative "something more premium" request
    # in a real, deterministic fact (recommend_properties can filter/prefer
    # is_premium=True) instead of asking the model to guess which of a host's
    # properties feels nicer. A host toggles this per property, same UI
    # pattern as the "Quote exact Airbnb price" switch already does for
    # exact_airbnb_pricing. Defaults false -- an existing property never
    # silently becomes "premium" just because this column now exists.
    is_premium: Mapped[bool] = mapped_column(default=False, server_default="false")

    # GPS coordinates for this exact Airbnb listing, fetched once via
    # SearchApi's airbnb_property engine and cached here permanently (a
    # listing's location doesn't change) -- pricing_engine.calculate_price
    # uses these to scope a tight bounding_box search on the airbnb search
    # engine for a live, date-scoped price for THIS listing specifically.
    # A plain city-only search returns a generic, unfiltered page of
    # listings for that city and this exact listing is often not even on
    # it (confirmed live -- a 20-listing city search for a real Colva
    # listing never included it); a tight bounding_box around its own
    # coordinates does. None until the first successful lookup.
    airbnb_latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    airbnb_longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))

    # Call Ownership Schedule (Phase 1 -- storage only, no resolver/routing
    # logic yet; see docs/call-ownership-schedule.md once written). Answers
    # "who owns an inbound guest call for this property" -- one of three
    # unambiguous states, not two:
    #   "MIRA"      -- Mira handles every call, unconditionally. Schedule
    #                  fields (below) are ignored even if set.
    #   "HOST"      -- the host receives every call directly, unconditionally
    #                  (once later phases implement routing). Schedule
    #                  fields are ignored even if set.
    #   "SCHEDULED" -- ownership alternates by time of day, per the schedule
    #                  fields below (during the host-hours window the HOST
    #                  owns the call; outside it, MIRA does -- see
    #                  resolve_effective_call_owner, added in a later
    #                  phase, for the actual HOST/MIRA decision).
    # Earlier draft of this column only had "MIRA"/"HOST" and tried to let
    # a *populated* schedule implicitly mean "scheduled" -- that was
    # genuinely ambiguous: "MIRA" with a schedule set could not be
    # distinguished from "always MIRA, schedule field populated but
    # inert" vs. "scheduled, MIRA currently active," and there was no way
    # to represent "scheduled" at all as a stored fact independent of the
    # current time. Fixed by making SCHEDULED an explicit third value
    # rather than an inferred state -- the three product-level modes now
    # map 1:1 onto three stored strings, no inference required anywhere
    # downstream. Plain string, not a DB enum, matching this codebase's
    # existing convention for every other status-like column (User.status,
    # CallSession.status, Lead.status, etc. -- see those models; no
    # SQLAlchemy/Postgres enum type is used anywhere in this project).
    # Defaults to "MIRA" so every existing property, and every property
    # created before a host visits the new settings UI, continues routing
    # to Mira exactly as it does today -- this default must never change
    # without a deliberate, separate decision, since flipping it silently
    # would route live guest calls to a host who never opted in.
    call_handling_mode: Mapped[str] = mapped_column(String(16), default="MIRA", server_default="MIRA")

    # The daily local-time window during which the HOST owns the call when
    # call_handling_mode == "SCHEDULED" (see resolve_effective_call_owner,
    # added in a later phase, for how these combine with call_handling_mode
    # into a HOST/MIRA decision; outside this window MIRA owns the call).
    # Meaningless/ignored when call_handling_mode is "MIRA" or "HOST" --
    # not validated against those modes at this layer (see PropertyUpdate's
    # validator in app/schemas/property.py for where that's enforced: a
    # SCHEDULED property must have both fields set, non-SCHEDULED properties
    # may leave them null or keep a previously-configured window around
    # inertly if the host switches modes and back). Plain "HH:MM" strings,
    # same representation and length as the existing check_in_time/
    # check_out_time columns above -- a SQL TIME column was considered and
    # rejected: these are recurring wall-clock values compared against a
    # call's local time-of-day, never arithmetic against a real date, so
    # the extra type precision buys nothing and HH:MM string comparison
    # ("22:00" > "09:00") already sorts correctly for same-day windows.
    # Overnight windows (e.g. start="22:00", end="06:00") are valid values
    # here and must remain so -- the start>end wraparound is resolved by
    # the future resolver, not rejected at this layer, same precedent as
    # seasonal_notes' own start_month/end_month wraparound above.
    call_handling_schedule_start: Mapped[str | None] = mapped_column(String(8))
    call_handling_schedule_end: Mapped[str | None] = mapped_column(String(8))

    # IANA timezone identifier the schedule window above is evaluated in
    # (e.g. "Asia/Kolkata") -- exact same column definition as the existing
    # User.timezone (see app/models/user.py), reused rather than inventing
    # a second timezone representation. Deliberately property-level, not
    # inherited from the host's own User.timezone: a host's properties can
    # span multiple regions/timezones even though today's fleet is
    # entirely IST, and the schedule is a property-local wall-clock
    # concept (guests call a specific property's line), not a host-global
    # one.
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")

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
    chunks: Mapped[list["PropertyChunk"]] = relationship(back_populates="property", cascade="all, delete-orphan")
