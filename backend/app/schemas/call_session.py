import uuid
from datetime import datetime

from pydantic import BaseModel


class CallSessionOut(BaseModel):
    id: uuid.UUID
    exotel_call_id: str | None
    vapi_call_id: str | None
    property_id: uuid.UUID | None
    guest_profile_id: uuid.UUID | None
    caller_number: str | None
    recording_url: str | None
    transcript: str | None
    ai_summary: str | None
    status: str
    urgency: str | None
    revenue_attributed: float
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
