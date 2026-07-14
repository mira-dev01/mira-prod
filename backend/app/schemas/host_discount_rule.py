import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TriggerType = Literal["no_ask", "guest_requests", "repeat_guest_same_host", "custom"]
RuleStatus = Literal["pending_validation", "approved", "rejected"]


class DiscountPolicyParseRequest(BaseModel):
    discount_policy_text: str = Field(min_length=1)


class HostDiscountRuleOut(BaseModel):
    id: uuid.UUID
    host_id: uuid.UUID
    trigger_type: str
    discount_percent: float
    source: str
    status: str
    raw_source_text: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DiscountPolicyParseResponse(BaseModel):
    """Drafts only -- nothing here has been applied to pricing yet. The host
    reviews these in the AI Training tab and approves/edits/rejects each one
    (see PATCH /host-discount-rules/{id})."""

    rules: list[HostDiscountRuleOut]


class HostDiscountRuleUpdate(BaseModel):
    trigger_type: TriggerType | None = None
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    status: RuleStatus | None = None
