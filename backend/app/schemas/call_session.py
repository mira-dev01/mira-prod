import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.call_summary import CallSummary
from app.schemas.notification import NotificationOut


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
    # Structured summary (see schemas/call_summary.py) -- NULL until
    # call_summary_service.summarize_call runs via on_pipeline_finished.
    ai_summary: CallSummary | None
    status: str
    urgency: str | None
    # Set by call_classification_service via on_pipeline_finished once the
    # call ends -- see schemas/call_classification.py for the taxonomy.
    call_type: str
    classification_confidence: float | None
    classification_reason: str | None
    revenue_attributed: float
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CallSessionDetailOut(CallSessionOut):
    # Notification rows tied to this call (escalations, WhatsApp sends,
    # etc.) -- only fetched for the single-call detail endpoint, not the
    # list endpoint, since it's an extra join a host paging through Calls
    # doesn't need on every row. See restructure.md Phase 1.1.
    escalations: list[NotificationOut]
