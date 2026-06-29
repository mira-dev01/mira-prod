import uuid
from datetime import datetime

from pydantic import BaseModel


class CallSessionOut(BaseModel):
    id: uuid.UUID
    exotel_call_id: str | None
    property_id: uuid.UUID | None
    guest_profile_id: uuid.UUID | None
    caller_number: str | None
    # guest_name/guest_phone prefer the Lead row (filled in via update_lead
    # during the call -- what the guest actually said) over the raw
    # caller_number/guest_profile, since caller_number is just the
    # signaling-level identity (e.g. "browser-test" for in-dashboard tests).
    guest_name: str | None
    guest_phone: str | None
    duration_minutes: float | None
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
