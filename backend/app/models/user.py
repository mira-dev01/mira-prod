from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(32), default="tier_1", server_default="tier_1")
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")

    lead_exophone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)

    # Host registration profile (self-reported, no verification against Airbnb).
    business_name: Mapped[str | None] = mapped_column(String(255))
    airbnb_host_status: Mapped[str | None] = mapped_column(String(32))
    property_count_estimate: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", server_default="Asia/Kolkata")
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    # Host Memory: host-level negotiation/policy preferences (see
    # memory-architecture-plan.md section 4). discount_policy_text is the
    # host's own free-text paragraph describing how they usually handle
    # discounts -- POST /auth/me/discount-policy/parse turns it into
    # structured, host-approved HostDiscountRule rows; this text field is
    # kept as-is for re-editing/re-parsing, not itself read by the pricing
    # engine. negotiation_allowed/max_discount_percent_override are read by
    # pricing_engine.negotiate_rate with a fallback to today's global
    # defaults (MAX_NEGOTIATION_DISCOUNT_PERCENT etc.) whenever unset --
    # never a behavior change for a host who hasn't configured anything.
    discount_policy_text: Mapped[str | None] = mapped_column(Text)
    negotiation_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    max_discount_percent_override: Mapped[float | None] = mapped_column(Numeric(5, 2))
    allow_pets: Mapped[bool | None] = mapped_column(Boolean)
    allow_early_checkin: Mapped[bool | None] = mapped_column(Boolean)
    follow_up_channel_preference: Mapped[str | None] = mapped_column(String(32))

    properties: Mapped[list["Property"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    leads: Mapped[list["Lead"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    faq_entries: Mapped[list["FaqEntry"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    discount_rules: Mapped[list["HostDiscountRule"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
