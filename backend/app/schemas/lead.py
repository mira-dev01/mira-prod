import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

LeadTemperature = Literal["hot", "warm", "cold"]
LeadStatus = Literal["open", "contacted", "booked", "closed"]


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
    status: str
    occasion: str | None
    created_at: datetime
    updated_at: datetime
    # Not a Lead column -- read off the most recent Notification sharing this
    # lead's call_session_id (see app/api/v1/leads.py). Notification is the
    # only place urgency (low/medium/high/emergency) is actually captured
    # today; surfacing it here lets the dashboard show one merged
    # "leads + live requests" view instead of two separate concepts. None
    # for a lead with no linked escalation.
    urgency: str | None = None

    model_config = {"from_attributes": True}


class LeadUpdate(BaseModel):
    guest_name: str | None = None
    lead_temperature: LeadTemperature | None = None
    next_follow_up: str | None = None
    conversation_summary: str | None = None
    transferred_to_host: bool | None = None
    status: LeadStatus | None = None
