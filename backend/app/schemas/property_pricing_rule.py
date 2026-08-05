import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.host_discount_rule import RuleStatus

RuleType = Literal["length_of_stay", "minimum_stay_nights", "early_checkin_fee", "late_checkout_fee", "custom"]


class PricingPolicyParseRequest(BaseModel):
    pricing_policy_text: str = Field(min_length=1)


class PropertyPricingRuleOut(BaseModel):
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


class PricingPolicyParseResponse(BaseModel):
    """Drafts only -- nothing here has been applied to pricing yet. The host
    reviews these in the AI Training tab (or the Pricing page's own copy of
    the same component), picks which properties each applies to, and
    approves/edits/rejects each one (see PATCH /property-pricing-rules/{id})."""

    rules: list[PropertyPricingRuleOut]


class PropertyPricingRuleUpdate(BaseModel):
    rule_type: RuleType | None = None
    condition: dict | None = None
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    label: str | None = None
    property_ids: list[str] | None = None
    status: RuleStatus | None = None
