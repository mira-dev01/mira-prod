"""The host's rules for how Mira handles a guest, edited on the Agent page.

One JSON document per host (users.agent_config), plus an optional per-property
override of the payment rules (properties.agent_config_overrides). Stored
partially: a missing key means "the default below", so adding a field or a
section later needs no migration and an existing host's behaviour never changes
until they choose something.

Name and phone are deliberately NOT here: they are required on every call and
are not configurable. Settings that already live elsewhere (discount policy,
handoff phrase, call hours) stay where they are and are not copied in.

Which phase makes each section do something:
  collect, hot_lead  -> the goal engine (Phase 4)
  booking, payment   -> payment links and date holds (Phase 9)
  busy_calls         -> busy-call recovery (app/services/recovery_service.py)
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# off = never ask; optional = ask, but carry on without it; required = ask, and
# do not move to the next step until the guest answers (or has been asked twice).
FieldMode = Literal["off", "optional", "required"]

HotSignal = Literal["availability_confirmed", "pricing_requested", "negotiation_initiated"]
ALL_HOT_SIGNALS: tuple[HotSignal, ...] = ("availability_confirmed", "pricing_requested", "negotiation_initiated")


class _Section(BaseModel):
    # Unknown keys (an older or newer client, a removed field) are dropped, not errors.
    model_config = ConfigDict(extra="ignore")


CustomKind = Literal["text", "time", "number", "yes_no"]
MAX_CUSTOM_FIELDS = 10
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def slugify(label: str) -> str:
    """"Arrival time" -> "arrival_time": a stable id the model can name in update_lead."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"field_{slug}" if slug else "field"
    return slug[:32].rstrip("_") or "field"


class CustomField(_Section):
    """One detail the host wants asked in their own words ("Arrival time"), beyond the built-in ones.

    The label is the host's text and reaches the model's prompt, so it is kept short and plain (one
    line, no control characters) and is always handed over as quoted data, never as an instruction.
    The id is made from the label when not given and never changes after, so renaming a label later
    does not orphan answers already saved."""

    id: str = ""
    label: str = Field(min_length=2, max_length=60)
    kind: CustomKind = "text"
    # A custom detail is either asked when natural ("optional") or must be known before availability or
    # a price is given ("required"). There is no "off": delete the field instead.
    mode: Literal["optional", "required"] = "optional"

    @field_validator("label")
    @classmethod
    def _plain_label(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) < 2:
            raise ValueError("A detail needs a name of at least 2 characters")
        if any(ord(ch) < 32 or ch in "{}<>`\\" for ch in value):
            raise ValueError("A detail name can only contain plain letters, numbers and punctuation")
        return value

    @model_validator(mode="after")
    def _make_id(self) -> "CustomField":
        if not self.id:
            self.id = slugify(self.label)
        if not _ID_RE.match(self.id):
            raise ValueError("Invalid detail id")
        return self


class CollectConfig(_Section):
    """What Mira asks the guest for, beyond the always-required name and phone."""

    dates: FieldMode = "optional"
    guests: FieldMode = "optional"
    purpose: FieldMode = "optional"
    location: FieldMode = "optional"
    budget: FieldMode = "optional"
    email: FieldMode = "off"
    # The host's own extra details ("Arrival time", "Any pets?"), asked and saved like the built-in ones.
    custom: list[CustomField] = Field(default_factory=list, max_length=MAX_CUSTOM_FIELDS)

    @model_validator(mode="after")
    def _unique_custom(self) -> "CollectConfig":
        seen_ids: set[str] = set()
        seen_labels: set[str] = set()
        reserved = {"dates", "guests", "purpose", "location", "budget", "email", "name", "phone", "guest_name"}
        out: list[CustomField] = []
        for item in self.custom:
            base = item.id
            if base in reserved:
                base = f"{base}_detail"
            unique, n = base, 2
            while unique in seen_ids:
                suffix = f"_{n}"
                unique, n = base[: 32 - len(suffix)] + suffix, n + 1
            if item.label.lower() in seen_labels:
                raise ValueError(f"There is already a detail called {item.label!r}")
            seen_ids.add(unique)
            seen_labels.add(item.label.lower())
            out.append(item.model_copy(update={"id": unique}) if unique != item.id else item)
        self.custom = out
        return self


HotRuleField = Literal["guests", "nights", "budget", "days_until_checkin"]
HotRuleOp = Literal["gte", "lte", "eq"]
MAX_HOT_RULES = 5


class HotRule(_Section):
    """A condition on what Mira has learned about the guest that makes the lead hot.

    `field` is a built-in number (guests, nights, budget, days_until_checkin) or a custom detail written
    "custom:<id>" (a number or yes/no one). Checked by code when a lead is saved, never by the model:
    "eq" is for yes/no details (value 1 = Yes, 0 = No)."""

    field: str = Field(max_length=40)
    op: HotRuleOp
    value: float = Field(ge=0, le=100_000_000)

    @field_validator("field")
    @classmethod
    def _known_field(cls, value: str) -> str:
        if value in ("guests", "nights", "budget", "days_until_checkin"):
            return value
        if value.startswith("custom:") and _ID_RE.match(value[len("custom:"):]):
            return value
        raise ValueError("Unknown field for a hot-lead rule")


class HotLeadConfig(_Section):
    """Which guest actions make a lead hot (a guest accepting a price and asking
    to book is always very hot), plus the host's own conditions."""

    hot_on: list[HotSignal] = Field(default_factory=lambda: list(ALL_HOT_SIGNALS))
    # Any one matching rule makes the lead hot (raise-only, like every other floor).
    rules: list[HotRule] = Field(default_factory=list, max_length=MAX_HOT_RULES)


class BookingConfig(_Section):
    # host: the host confirms every booking by hand.
    # payment: a verified payment confirms it.
    confirm_by: Literal["host", "payment"] = "host"


class PaymentConfig(_Section):
    kind: Literal["full", "split"] = "full"
    # Share of the total paid up front when kind == "split".
    split_percent: int = Field(default=30, ge=1, le=99)
    balance_due_days_before_checkin: int = Field(default=7, ge=0, le=90)
    # How long a sent payment link holds the guest's dates.
    hold_minutes: int = Field(default=60, ge=5, le=1440)


class BusyCallConfig(_Section):
    """What happens to a guest who rang while Mira was already on a call."""

    # host_follows_up: the guest is told the host will get back to them and the
    #   host gets a ticket to do so; Mira does not message them again.
    # callback_invite: when Mira is free again she messages the guest to call
    #   back; if they have not rung within callback_wait_minutes, the host gets
    #   a ticket to reach out. (Today's behaviour, so it is the default.)
    mode: Literal["host_follows_up", "callback_invite"] = "callback_invite"
    callback_wait_minutes: int = Field(default=5, ge=1, le=60)


class AgentConfig(_Section):
    version: Literal[1] = 1
    collect: CollectConfig = Field(default_factory=CollectConfig)
    hot_lead: HotLeadConfig = Field(default_factory=HotLeadConfig)
    booking: BookingConfig = Field(default_factory=BookingConfig)
    payment: PaymentConfig = Field(default_factory=PaymentConfig)
    busy_calls: BusyCallConfig = Field(default_factory=BusyCallConfig)


class PaymentOverride(_Section):
    """A property's departures from the portfolio payment rules. Only the
    fields that are set differ; everything else follows the portfolio."""

    kind: Literal["full", "split"] | None = None
    split_percent: int | None = Field(default=None, ge=1, le=99)
    balance_due_days_before_checkin: int | None = Field(default=None, ge=0, le=90)
    hold_minutes: int | None = Field(default=None, ge=5, le=1440)

    @model_validator(mode="after")
    def _not_empty(self) -> "PaymentOverride":
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("An override must change at least one payment setting")
        return self


class AgentConfigUpdate(BaseModel):
    """Partial update: only the sections sent are replaced (each as a whole)."""

    model_config = ConfigDict(extra="forbid")

    collect: CollectConfig | None = None
    hot_lead: HotLeadConfig | None = None
    booking: BookingConfig | None = None
    payment: PaymentConfig | None = None
    busy_calls: BusyCallConfig | None = None


class AgentConfigOut(BaseModel):
    config: AgentConfig
    # property_id -> that property's payment override (only properties that have one).
    payment_overrides: dict[str, PaymentOverride]
