from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # Nullable as of the Clerk cutover -- no longer written or read by
    # get_current_user (app/auth/security.py's bcrypt code is gone). Kept
    # rather than dropped outright so existing rows' historical hashes
    # aren't destroyed in the same migration as the auth-boundary rewrite;
    # safe to drop in a later cleanup pass once Clerk is confirmed stable.
    hashed_password: Mapped[str | None] = mapped_column(String(255))

    # Clerk migration -- new User rows are now created directly against
    # this identity (see app/auth/dependencies.py's _resolve_local_user).
    # Existing rows get linked by matching email on first Clerk sign-in, and
    # hashed_password is dropped in the same follow-up migration that makes
    # this column load-bearing.
    clerk_user_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)

    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(32), default="tier_1", server_default="tier_1")
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")

    lead_exophone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    # Twilio equivalent of lead_exophone above -- see the comment on
    # Property.twilio_number for why this exists as a separate field.
    twilio_lead_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)

    # Host registration profile (self-reported, no verification against Airbnb).
    business_name: Mapped[str | None] = mapped_column(String(255))
    airbnb_host_status: Mapped[str | None] = mapped_column(String(32))
    property_count_estimate: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Host Profile page (step-by-step completion, not required at signup --
    # see POST /auth/me/photo, same Cloudinary upload pattern as property
    # photos). photo_url is the host's own picture, shown in the sidebar and
    # profile page; None means "no photo set", frontend falls back to
    # initials. whatsapp_assist_enabled is a simple on/off placeholder for
    # now -- deliberately not a richer config shape yet, since which
    # WhatsApp features actually matter is still being worked out from host
    # testing.
    photo_url: Mapped[str | None] = mapped_column(String(512))
    # Cover/banner image for the profile page's hero section. Same optional,
    # add-it-whenever pattern as photo_url -- None just means the hero shows
    # an empty placeholder inviting the host to add one later.
    banner_url: Mapped[str | None] = mapped_column(String(512))
    whatsapp_assist_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Where escalation summaries (app/integrations/email_client.py, fired
    # from handle_escalate_to_host) get sent. None -- the common case -- means
    # "use the login email above"; set this when the host wants escalations
    # routed to a different inbox (e.g. a shared front-desk address) without
    # changing their login email.
    notification_email: Mapped[str | None] = mapped_column(String(255))

    # Per-host voice agent customization (see app/prompts/system_prompt.py).
    # All optional -- None means "use Mira's default". agent_first_message
    # supports {host_name}, {property_name}, {city}, {guest_name} placeholders;
    # any placeholder that doesn't apply to the current call (e.g.
    # {property_name} on a Lead Agent call with no property selected yet)
    # resolves to "" rather than raising.
    agent_first_message: Mapped[str | None] = mapped_column(Text)
    agent_persona: Mapped[str | None] = mapped_column(Text)
    agent_escalation_phrase: Mapped[str | None] = mapped_column(Text)
    # Spoken once, deterministically (TTSSpeakFrame, never LLM-generated),
    # right before a live call is transferred to the host after they tap
    # "Take Call" on the guest_calling WhatsApp -- see
    # app/voice/pipeline.py's _wait_and_trigger_handoff and
    # documentation/host-call-hours-and-handoff.md. None means "use
    # DEFAULT_HOST_HANDOFF_PHRASE" (app/prompts/system_prompt.py). Distinct
    # from agent_escalation_phrase: escalation = the host follows up later;
    # this = the host is joining the live call now. A "loop in the host"
    # variant is rejected at write time (app/schemas/user.py) and falls
    # back to the default at read time regardless.
    agent_handoff_phrase: Mapped[str | None] = mapped_column(Text)
    # "female" | "male" -- maps to a specific Sarvam bulbul:v3 speaker name in
    # app/voice/pipeline.py (VOICE_BY_GENDER). Default "female" matches the
    # pre-existing global SARVAM_TTS_SPEAKER default ("roopa").
    agent_voice_gender: Mapped[str] = mapped_column(String(16), default="female", server_default="female")

    # Phase 3.3 (documentation/agent-conversation-improvement.md) -- "adaptive"
    # (None) | "hindi_first" | "english_first". None/unset (the default for
    # every existing host) means today's unchanged adaptive-mirroring
    # behavior -- GOLDEN_RULES' passive code-switch mirroring and explicit-
    # preference override both still apply exactly as before regardless of
    # this field. This exists so a host running a market where guests
    # overwhelmingly prefer plain Hindi (or English) -- e.g. a homestay in
    # rural Uttarakhand or Varanasi -- can set a baseline the per-call
    # adaptive behavior still layers on top of, rather than this codebase
    # structurally assuming urban-Hinglish-first is the only supported
    # market (the goal's own "any host in India" framing).
    agent_language_policy: Mapped[str | None] = mapped_column(String(16))

    # Host Memory: host-level negotiation/policy preferences (see
    # memory-architecture-plan.md section 4). discount_policy_text is the
    # host's own free-text paragraph describing how they usually handle
    # discounts -- POST /negotiation-rules/parse turns it into structured,
    # host-approved NegotiationRule rows; this text field is kept as-is for
    # re-editing/re-parsing, not itself read by the pricing engine.
    # negotiation_allowed/max_discount_percent_override are read by
    # pricing_engine.negotiate_rate with a fallback to today's global
    # defaults (MAX_NEGOTIATION_DISCOUNT_PERCENT etc.) whenever unset --
    # never a behavior change for a host who hasn't configured anything.
    discount_policy_text: Mapped[str | None] = mapped_column(Text)
    negotiation_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    max_discount_percent_override: Mapped[float | None] = mapped_column(Numeric(5, 2))
    allow_pets: Mapped[bool | None] = mapped_column(Boolean)
    allow_early_checkin: Mapped[bool | None] = mapped_column(Boolean)
    follow_up_channel_preference: Mapped[str | None] = mapped_column(String(32))

    # Account-global host call hours (see
    # documentation/host-call-hours-and-handoff.md and
    # app/services/call_ownership.py). One time-of-day window, common to
    # every property on the account, during which inbound guest calls are
    # routed to the host's phone (User.phone) instead of Mira. Deliberately
    # account-level, not per-property: replaces the per-property
    # Property.call_handling_mode/schedule columns as the live routing
    # input.
    #   host_call_hours_enabled: master switch. False (the default for
    #     every existing row) == Mira answers 24/7, exactly today's
    #     behavior when the FIXED_HOST_HOURS_* env override is unset.
    #   host_call_hours_start/_end: "HH:MM" 24-hour strings. An overnight
    #     window (start > end, e.g. "22:00"->"06:00") is valid and wraps
    #     past midnight -- resolve_effective_call_owner handles it.
    #   host_call_hours_timezone: IANA identifier the window is evaluated
    #     in. Its own column rather than reusing User.timezone (which has
    #     no IANA validator and is a display/date anchor) -- same reasoning
    #     Property.timezone's own comment gives.
    # start/end are only read when host_call_hours_enabled is True;
    # app/schemas/user.py's UserUpdate requires both to be set in the same
    # request that flips the switch on.
    host_call_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    host_call_hours_start: Mapped[str | None] = mapped_column(String(8))
    host_call_hours_end: Mapped[str | None] = mapped_column(String(8))
    host_call_hours_timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Kolkata", server_default="Asia/Kolkata"
    )

    properties: Mapped[list["Property"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    leads: Mapped[list["Lead"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    faq_entries: Mapped[list["FaqEntry"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    negotiation_rules: Mapped[list["NegotiationRule"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
