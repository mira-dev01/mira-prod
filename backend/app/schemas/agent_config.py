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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# off = never ask; optional = ask, but carry on without it; required = ask, and
# do not move to the next step until the guest answers (or has been asked twice).
FieldMode = Literal["off", "optional", "required"]

HotSignal = Literal["availability_confirmed", "pricing_requested", "negotiation_initiated"]
ALL_HOT_SIGNALS: tuple[HotSignal, ...] = ("availability_confirmed", "pricing_requested", "negotiation_initiated")


class _Section(BaseModel):
    # Unknown keys (an older or newer client, a removed field) are dropped, not errors.
    model_config = ConfigDict(extra="ignore")


class CollectConfig(_Section):
    """What Mira asks the guest for, beyond the always-required name and phone."""

    dates: FieldMode = "optional"
    guests: FieldMode = "optional"
    purpose: FieldMode = "optional"
    location: FieldMode = "optional"
    budget: FieldMode = "optional"
    email: FieldMode = "off"


class HotLeadConfig(_Section):
    """Which guest actions make a lead hot (a guest accepting a price and asking
    to book is always very hot)."""

    hot_on: list[HotSignal] = Field(default_factory=lambda: list(ALL_HOT_SIGNALS))


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
