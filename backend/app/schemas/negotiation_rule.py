import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# The three former HostDiscountRule.trigger_type values (host-wide
# negotiation triggers) plus the four former PropertyPricingRule.rule_type
# values (stay-pricing rules) plus "custom" -- one flat enum for the merged
# table. See NegotiationRule's docstring for the full mapping/rationale.
RuleType = Literal[
    "discount_no_ask",
    "discount_guest_requests",
    "discount_repeat_guest",
    "length_of_stay",
    "minimum_stay_nights",
    "early_checkin_fee",
    "late_checkout_fee",
    "custom",
]
RuleStatus = Literal["pending_validation", "approved", "rejected"]

DISCOUNT_TRIGGER_RULE_TYPES = {"discount_no_ask", "discount_guest_requests", "discount_repeat_guest"}


class NegotiationStage(BaseModel):
    """One entry in an OPTIONAL, host-defined negotiation ladder (Phase 4D,
    see documentation design docs "Phase 4B: Generalized Negotiation Policy
    Model" / "Phase 4C: Negotiation Semantics Contract"). `order` is the
    host-defined sequence position (0-based); `value` is that stage's
    authorized discount_percent-shaped value -- same unit/meaning as
    NegotiationRule.discount_percent, just one of an ordered list instead
    of a single scalar. No fixed count or value is assumed anywhere this
    type is used -- a host may configure zero, one, or arbitrarily many
    stages with arbitrary values."""

    order: int = Field(ge=0)
    value: float = Field(ge=0, le=100)


def _validate_stages(value: list[NegotiationStage] | None) -> list[NegotiationStage] | None:
    if value is None:
        return None
    orders = [stage.order for stage in value]
    if len(orders) != len(set(orders)):
        raise ValueError("stages must have unique order values")
    return sorted(value, key=lambda stage: stage.order)


class NegotiationPolicyParseRequest(BaseModel):
    policy_text: str = Field(min_length=1)


class NegotiationRuleOut(BaseModel):
    id: uuid.UUID
    host_id: uuid.UUID
    rule_type: str
    condition: dict
    discount_percent: float | None
    label: str | None
    property_ids: list[str]
    source: str
    status: str
    raw_source_text: str | None
    created_at: datetime
    # Phase 4D: optional, nullable -- None for every rule authored before
    # this phase and for any host who never configures a staged ladder.
    # See NegotiationStage's own docstring; no default stage count/value.
    stages: list[NegotiationStage] | None = None

    _validate_stages = field_validator("stages")(_validate_stages)

    model_config = {"from_attributes": True}


class NegotiationPolicyParseResponse(BaseModel):
    """Drafts only -- nothing here has been applied to pricing/negotiation
    yet. The host reviews these in the AI Training tab, picks which
    properties a stay-pricing rule applies to (discount triggers are
    host-wide and skip that step), and approves/edits/rejects each one (see
    PATCH /negotiation-rules/{id})."""

    rules: list[NegotiationRuleOut]


class NegotiationRuleUpdate(BaseModel):
    rule_type: RuleType | None = None
    condition: dict | None = None
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    label: str | None = None
    property_ids: list[str] | None = None
    status: RuleStatus | None = None
    # Phase 4D: optional -- omitted/None means "don't touch stages" for a
    # PATCH the same way every other None field here already means
    # "unchanged" (see the route's exclude_unset handling). Explicitly
    # setting stages=[] (not None) is how a host-facing future edit would
    # ever CLEAR a previously-configured ladder back to flat-only -- no such
    # UI exists yet (see Phase 4C Section J), this is schema-level headroom
    # only, not a proposal to build that flow now.
    stages: list[NegotiationStage] | None = None

    _validate_stages = field_validator("stages")(_validate_stages)
