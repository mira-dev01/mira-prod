import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
