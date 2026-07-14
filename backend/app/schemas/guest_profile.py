import uuid
from datetime import datetime

from pydantic import BaseModel


class ConversationSummaryEntry(BaseModel):
    call_session_id: uuid.UUID
    property_id: uuid.UUID | None
    property_name: str | None
    date: datetime
    summary: str
    lead_temperature: str | None


class GuestProfileOut(BaseModel):
    id: uuid.UUID
    phone: str
    name: str | None
    total_stays: int
    preferences: dict
    notes: str | None
    last_property_id: uuid.UUID | None
    preferred_language: str | None
    last_outcome: str | None
    last_follow_up: str | None
    last_call_at: datetime | None
    conversation_summaries: list[ConversationSummaryEntry]
    created_at: datetime

    model_config = {"from_attributes": True}


class GuestProfileUpdate(BaseModel):
    name: str | None = None
    preferences: dict | None = None
    notes: str | None = None


class GuestRecentCall(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID | None
    property_name: str | None
    status: str
    ai_summary: str | None
    started_at: datetime | None

    model_config = {"from_attributes": True}


class GuestProfileDetailOut(GuestProfileOut):
    # SUM(CallSession.revenue_attributed) for every call tied to this guest
    # -- a real aggregation of an existing field, not a new metric. Only
    # counts calls scoped to the requesting host (a guest can, in principle,
    # be shared across hosts by phone number; this must never leak another
    # host's revenue into the total).
    lifetime_revenue: float
    recent_calls: list[GuestRecentCall]
