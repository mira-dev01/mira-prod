import re
import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

# Same pattern app/voice/escalation_phrase_guard.py originally detected
# (before that guard moved to unconditional replacement, see its module
# docstring) -- reused here to reject the banned phrasing at the API
# boundary instead of only papering over it live on the call. Confirmed
# live: a host set agent_escalation_phrase to this exact wording, and Mira
# dutifully spoke it verbatim (GOLDEN_RULES's ban only covers what the model
# generates itself, not a host-authored line it's instructed to recite).
_LOOP_IN_HOST_RE = re.compile(r"loop\w*.{0,15}host|host.{0,15}loop", re.IGNORECASE)

# "HH:MM" 24-hour, 00:00-23:59. Same pattern as app/schemas/property.py's
# _HH_MM_RE for the per-property call_handling_schedule fields -- duplicated
# rather than shared for the same reason _LOOP_IN_HOST_RE is duplicated
# across this file and system_prompt.py (a tiny, stable regex, not worth a
# shared-module import dependency between two schema files).
_HH_MM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

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

# Maps to a specific Sarvam bulbul:v3 speaker name in
# app/voice/pipeline.py's VOICE_BY_GENDER, not a Sarvam-native concept --
# bulbul:v3's speaker enum carries no gender metadata itself.
AgentVoiceGender = Literal["female", "male"]

# Phase 3.3 (documentation/agent-conversation-improvement.md) -- None/unset
# means today's unchanged adaptive-mirroring behavior. See
# app/models/user.py's agent_language_policy column docstring.
AgentLanguagePolicy = Literal["hindi_first", "english_first"]


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
    twilio_lead_number: str | None = None
    business_name: str | None = None
    airbnb_host_status: AirbnbHostStatus | None = None
    property_count_estimate: int | None = Field(default=None, ge=1)
    timezone: str | None = None
    agent_first_message: str | None = None
    agent_persona: str | None = None
    agent_escalation_phrase: str | None = None
    agent_handoff_phrase: str | None = None
    agent_voice_gender: AgentVoiceGender | None = None
    agent_language_policy: AgentLanguagePolicy | None = None
    notification_email: EmailStr | None = None
    # Account-global host call hours -- see app/models/user.py and
    # documentation/host-call-hours-and-handoff.md. start/end are "HH:MM"
    # 24-hour strings; the timezone is an IANA identifier. Format-checked
    # below; the cross-field rule (enabling requires both bounds in the
    # same request) is the model_validator further down.
    host_call_hours_enabled: bool | None = None
    host_call_hours_start: str | None = None
    host_call_hours_end: str | None = None
    host_call_hours_timezone: str | None = None
    # Host Memory (see memory-architecture-plan.md section 4). Setting
    # discount_policy_text alone does NOT change pricing -- it's just the
    # host's raw text, parsed into NegotiationRule drafts via
    # POST /negotiation-rules/parse, which still need host approval
    # (status="approved") before pricing_engine reads them.
    discount_policy_text: str | None = None
    negotiation_allowed: bool | None = None
    max_discount_percent_override: float | None = Field(default=None, ge=0, le=100)
    allow_pets: bool | None = None
    allow_early_checkin: bool | None = None
    follow_up_channel_preference: str | None = None
    whatsapp_assist_enabled: bool | None = None

    @field_validator("agent_escalation_phrase", "agent_handoff_phrase")
    @classmethod
    def _reject_loop_in_host_phrasing(cls, value: str | None) -> str | None:
        if value is not None and _LOOP_IN_HOST_RE.search(value):
            raise ValueError(
                'this phrase cannot contain a "loop in the host" variant -- Mira is never '
                "allowed to say this, so it can never actually be spoken on a call. Use wording "
                "that names a real next step instead, e.g. \"One moment, let me get the host.\""
            )
        return value

    @field_validator("host_call_hours_start", "host_call_hours_end", "host_call_hours_timezone")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        # A cleared <input> posts "" -- normalize to None so it stores as
        # NULL, matching app/schemas/property.py's _blank_exophone_is_none.
        # Runs before the format/IANA checks below (Pydantic applies
        # validators for a field top-to-bottom), so those only ever see a
        # real value or None.
        return value or None

    @field_validator("host_call_hours_start", "host_call_hours_end")
    @classmethod
    def _check_hh_mm_format(cls, value: str | None) -> str | None:
        # Format only -- "is this a well-formed 24-hour HH:MM string". The
        # window's meaning (same-day vs overnight wrap) is
        # resolve_effective_call_owner's job, exactly as the equivalent
        # per-property validator in app/schemas/property.py notes.
        if value is not None and not _HH_MM_RE.match(value):
            raise ValueError("must be in HH:MM 24-hour format, e.g. '09:00' or '22:00'")
        return value

    @field_validator("host_call_hours_timezone")
    @classmethod
    def _check_host_call_hours_timezone(cls, value: str | None) -> str | None:
        # Real IANA-identifier check via the stdlib zoneinfo database (no
        # new dependency; already used the same way in
        # app/schemas/property.py and app/services/call_ownership.py). A
        # garbage value here would otherwise only fail deep inside
        # resolve_effective_call_owner on a live inbound call.
        if value is not None:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError:
                raise ValueError(f"'{value}' is not a valid IANA timezone identifier, e.g. 'Asia/Kolkata'")
        return value

    @model_validator(mode="after")
    def _check_host_call_hours_window_when_enabled(self) -> "UserUpdate":
        # Cross-field: turning the window ON is meaningless without both
        # bounds. Only enforced when THIS request sets
        # host_call_hours_enabled to True, and it requires THIS same
        # request to also carry both start and end (a non-empty "HH:MM"
        # each) -- mirrors app/schemas/property.py's
        # _check_schedule_required_for_scheduled_mode: this validator sees
        # only the request payload, not the persisted row, so a host
        # enabling the window must (re-)supply both times in the same PATCH.
        # A request that leaves host_call_hours_enabled unset (the common
        # case -- editing an unrelated field) never trips this.
        if self.host_call_hours_enabled is True:
            start = (self.host_call_hours_start or "").strip()
            end = (self.host_call_hours_end or "").strip()
            if not start or not end:
                raise ValueError(
                    "host_call_hours_start and host_call_hours_end are both required when "
                    "host_call_hours_enabled is set to true"
                )
        return self


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
    twilio_lead_number: str | None
    business_name: str | None
    airbnb_host_status: str | None
    property_count_estimate: int | None
    timezone: str
    terms_accepted_at: datetime | None
    agent_first_message: str | None
    agent_persona: str | None
    agent_escalation_phrase: str | None
    agent_handoff_phrase: str | None
    agent_voice_gender: AgentVoiceGender
    agent_language_policy: AgentLanguagePolicy | None
    notification_email: str | None
    host_call_hours_enabled: bool
    host_call_hours_start: str | None
    host_call_hours_end: str | None
    host_call_hours_timezone: str
    discount_policy_text: str | None
    negotiation_allowed: bool
    max_discount_percent_override: float | None
    allow_pets: bool | None
    allow_early_checkin: bool | None
    follow_up_channel_preference: str | None
    photo_url: str | None
    banner_url: str | None
    whatsapp_assist_enabled: bool

    model_config = {"from_attributes": True}


class HostOnboardingResponse(BaseModel):
    """The Bright Data snapshot_id for the first property's scrape, still
    running when this is returned -- POST /auth/onboarding doesn't block on
    it. The frontend polls GET /properties/import-airbnb-urls/{snapshot_id}
    exactly as the existing "Import from Airbnb" dialog does."""

    snapshot_id: str | None = None
    import_error: str | None = None
