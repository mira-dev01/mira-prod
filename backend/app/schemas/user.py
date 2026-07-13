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


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = None
    phone: str | None = None


class HostRegistration(BaseModel):
    """Richer signup used by POST /auth/register-host. Kept separate from
    UserCreate so the existing minimal /auth/register endpoint (and anything
    already calling it) is untouched."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str
    phone: str | None = None
    business_name: str | None = None
    business_phone: str = Field(min_length=1, max_length=32)
    airbnb_host_status: AirbnbHostStatus | None = None
    property_count_estimate: int | None = Field(default=None, ge=1)
    airbnb_url: str = Field(min_length=1)
    ical_url: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


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


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HostRegistrationResponse(BaseModel):
    """Token plus the Bright Data snapshot_id for the first property's
    scrape, which is still running when this is returned (see
    POST /auth/register-host -- registration doesn't block on the scrape).
    The frontend polls GET /properties/import-airbnb-urls/{snapshot_id}
    exactly as the existing "Import from Airbnb" dialog does."""

    access_token: str
    token_type: str = "bearer"
    snapshot_id: str | None = None
    import_error: str | None = None
