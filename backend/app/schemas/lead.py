import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

LeadTemperature = Literal["hot", "warm", "cold"]


class LeadOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    call_session_id: uuid.UUID | None
    guest_name: str | None
    phone: str | None
    email: str | None
    check_in: date | None
    check_out: date | None
    num_guests: int | None
    purpose_of_stay: str | None
    budget: float | None
    preferred_location: str | None
    properties_discussed: list
    questions_asked: list
    support_requests: list
    lead_temperature: str | None
    lead_source: str
    conversation_summary: str | None
    next_follow_up: str | None
    escalated: bool
    transferred_to_host: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadUpdate(BaseModel):
    guest_name: str | None = None
    lead_temperature: LeadTemperature | None = None
    next_follow_up: str | None = None
    conversation_summary: str | None = None
    transferred_to_host: bool | None = None
