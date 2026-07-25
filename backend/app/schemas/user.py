import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# Self-reported only -- MIRA has no Airbnb API access to verify this.
# Collapses Airbnb's actual (overlapping, quarterly-refreshed) badge system
# -- Superhost, the newer listing-level "Guest Favorite", and the
# Individual vs. Professional host business classification -- into one
# single-select field for the registration form.
AirbnbHostStatus = Literal[
    "new_host",
    "individual_host",
    "superhost",
    "guest_favorite",
    "professional_host",
    "prefer_not_to_say",
]


class HostOnboarding(BaseModel):
    """Business/Airbnb-import data collected on the post-signup onboarding
    page, once a host already has a Clerk-authenticated account (see
    POST /auth/onboarding). Clerk itself owns identity (email/password) --
    this schema only carries the fields Clerk's sign-up form knows nothing
    about."""

    name: str
    phone: str | None = None
    business_name: str | None = None
    business_phone: str = Field(min_length=1, max_length=32)
    airbnb_host_status: AirbnbHostStatus | None = None
    property_count_estimate: int | None = Field(default=None, ge=1)
    airbnb_url: str = Field(min_length=1)
    ical_url: str | None = None
    agent_first_message: str | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    lead_exophone: str | None = None
    business_name: str | None = None
    airbnb_host_status: AirbnbHostStatus | None = None
    property_count_estimate: int | None = Field(default=None, ge=1)
    timezone: str | None = None
    agent_first_message: str | None = None
    agent_persona: str | None = None
    agent_escalation_phrase: str | None = None
    notification_email: EmailStr | None = None
    # Host Memory (see memory-architecture-plan.md section 4). Setting
    # discount_policy_text alone does NOT change pricing -- it's just the
    # host's raw text, parsed into HostDiscountRule drafts via
    # POST /auth/me/discount-policy/parse, which still need host approval
    # (status="approved") before pricing_engine reads them.
    discount_policy_text: str | None = None
    negotiation_allowed: bool | None = None
    max_discount_percent_override: float | None = Field(default=None, ge=0, le=100)
    allow_pets: bool | None = None
    allow_early_checkin: bool | None = None
    follow_up_channel_preference: str | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str | None
    phone: str | None
    tier: str
    status: str
    # Whether the request's Clerk org matches settings.clerk_dev_org_id --
    # gates dev-only features (currently just "Talk to Mira") on the
    # frontend. Set as a dynamic attribute in get_current_user, never
    # persisted; defaults to False for anything that bypasses that (e.g. a
    # UserOut built manually).
    is_internal_org: bool = False
    lead_exophone: str | None
    business_name: str | None
    airbnb_host_status: str | None
    property_count_estimate: int | None
    timezone: str
    terms_accepted_at: datetime | None
    agent_first_message: str | None
    agent_persona: str | None
    agent_escalation_phrase: str | None
    notification_email: str | None
    discount_policy_text: str | None
    negotiation_allowed: bool
    max_discount_percent_override: float | None
    allow_pets: bool | None
    allow_early_checkin: bool | None
    follow_up_channel_preference: str | None

    model_config = {"from_attributes": True}


class HostOnboardingResponse(BaseModel):
    """The Bright Data snapshot_id for the first property's scrape, still
    running when this is returned -- POST /auth/onboarding doesn't block on
    it. The frontend polls GET /properties/import-airbnb-urls/{snapshot_id}
    exactly as the existing "Import from Airbnb" dialog does."""

    snapshot_id: str | None = None
    import_error: str | None = None
