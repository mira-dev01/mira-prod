import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FAQItem(BaseModel):
    question: str
    answer: str


class PropertyCreate(BaseModel):
    name: str
    city: str | None = None
    exophone: str | None = None
    base_price: float = Field(ge=0)
    ical_url: str | None = None
    house_rules: str | None = None
    faq: list[FAQItem] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)
    check_in_time: str = "14:00"
    check_out_time: str = "11:00"
    max_guests: int = 4


class PropertyUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    exophone: str | None = None
    base_price: float | None = None
    ical_url: str | None = None
    house_rules: str | None = None
    faq: list[FAQItem] | None = None
    amenities: list[str] | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    max_guests: int | None = None


class PropertyOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    city: str | None
    exophone: str | None
    base_price: float
    ical_url: str | None
    house_rules: str | None
    faq: list[dict]
    amenities: list[str]
    check_in_time: str
    check_out_time: str
    max_guests: int
    vapi_assistant_id: str | None
    vapi_phone_number_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
