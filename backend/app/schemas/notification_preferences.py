"""How a host wants to be told about -- and pulled into -- guest calls.

Stored as one JSONB document on User.notification_preferences (read on the
live-call path wherever the host User is already loaded, so no extra
query). Every default equals the behaviour before these settings existed,
so a host who never opens the panel sees no change. What can never be
switched off, regardless of anything here: the in-app notification and the
lead record for every escalation, transfer and busy call -- a genuine guest
opportunity must not silently disappear.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Placeholders a host can use in the call-summary email subject/body.
# Rendered by plain substitution (never str.format) and HTML-escaped.
TEMPLATE_PLACEHOLDERS: tuple[str, ...] = (
    "guest_name",
    "guest_phone",
    "property_name",
    "lead_label",
    "call_summary",
    "call_time",
    "call_duration",
    "dashboard_link",
)
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")

DEFAULT_CALL_SUMMARY_SUBJECT = "[{lead_label}] Call summary: {property_name}"
# Offered as the starting point when a host chooses to customise the body;
# an unset body keeps the built-in email layout.
DEFAULT_CALL_SUMMARY_BODY = (
    "{call_summary}\n\n"
    "Guest: {guest_name} ({guest_phone})\n"
    "Property: {property_name}\n"
    "Call: {call_time}, {call_duration}"
)


def check_template(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    unknown = sorted({m for m in _PLACEHOLDER_RE.findall(value) if m not in TEMPLATE_PLACEHOLDERS})
    if unknown:
        raise ValueError(
            "Unknown placeholder "
            + ", ".join("{" + u + "}" for u in unknown)
            + ". Available: "
            + ", ".join("{" + p + "}" for p in TEMPLATE_PLACEHOLDERS)
        )
    return value


def render_template(template: str, values: dict[str, str | None]) -> str:
    """Known placeholders become their value ("" when unknown for this
    call -- same rule as the agent intro message); check_template has
    already rejected anything else at save time."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1)) or "", template)


class LeadLabels(BaseModel):
    """The host's own names for the four lead tiers. hot also covers the
    internal very_hot level; not_qualified is a lead with no temperature."""

    model_config = ConfigDict(extra="ignore")

    hot: str = "Hot"
    warm: str = "Warm"
    cold: str = "Cold"
    not_qualified: str = "Not qualified"

    @field_validator("hot", "warm", "cold", "not_qualified")
    @classmethod
    def _non_empty_short(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 24:
            raise ValueError("each label must be 1-24 characters")
        return value

    @model_validator(mode="after")
    def _distinct(self) -> "LeadLabels":
        labels = [self.hot, self.warm, self.cold, self.not_qualified]
        if len({label.lower() for label in labels}) != len(labels):
            raise ValueError("lead labels must all be different")
        return self


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Who live transfers and alerts reach: one number for the whole
    # account (User.phone), or a number per group of properties
    # (Property.host_transfer_phone, falling back to User.phone).
    transfer_number_mode: Literal["single", "per_property"] = "single"
    call_summary_email: bool = True
    escalation_email: bool = True
    call_summary_subject: str | None = Field(default=None, max_length=200)
    call_summary_body: str | None = Field(default=None, max_length=4000)
    # In-stay problems and questions Mira can't answer.
    stay_request_handling: Literal["whatsapp", "live_transfer"] = "whatsapp"
    # A guest asking to speak to the host.
    connect_request_handling: Literal["live_transfer", "whatsapp"] = "live_transfer"
    busy_call_alert: bool = True
    guest_calling_alert: bool = True
    guest_reply_alert: bool = True
    lead_labels: LeadLabels = Field(default_factory=LeadLabels)

    @field_validator("call_summary_subject", "call_summary_body")
    @classmethod
    def _valid_template(cls, value: str | None) -> str | None:
        return check_template(value)


class NotificationPreferencesUpdate(BaseModel):
    """Partial update -- only the fields sent change."""

    model_config = ConfigDict(extra="forbid")

    transfer_number_mode: Literal["single", "per_property"] | None = None
    call_summary_email: bool | None = None
    escalation_email: bool | None = None
    call_summary_subject: str | None = Field(default=None, max_length=200)
    call_summary_body: str | None = Field(default=None, max_length=4000)
    stay_request_handling: Literal["whatsapp", "live_transfer"] | None = None
    connect_request_handling: Literal["live_transfer", "whatsapp"] | None = None
    busy_call_alert: bool | None = None
    guest_calling_alert: bool | None = None
    guest_reply_alert: bool | None = None
    lead_labels: LeadLabels | None = None

    @field_validator("call_summary_subject", "call_summary_body")
    @classmethod
    def _valid_template(cls, value: str | None) -> str | None:
        return check_template(value)


class NotificationSettingsOut(BaseModel):
    preferences: NotificationPreferences
    placeholders: list[str]
    default_subject: str
    default_body: str


class EmailPreviewIn(BaseModel):
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=4000)

    @field_validator("subject", "body")
    @classmethod
    def _valid_template(cls, value: str | None) -> str | None:
        return check_template(value)


class EmailPreviewOut(BaseModel):
    subject: str
    html: str
