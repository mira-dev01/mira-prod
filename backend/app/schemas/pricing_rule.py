import uuid
from datetime import datetime

from pydantic import BaseModel


class PricingRuleCreate(BaseModel):
    property_id: uuid.UUID
    rule_type: str
    condition: dict = {}
    discount_percent: float
    active: bool = True


class PricingRuleOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    rule_type: str
    condition: dict
    discount_percent: float
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
