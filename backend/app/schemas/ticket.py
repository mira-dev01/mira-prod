import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

TicketKind = Literal["escalation", "transfer", "technician", "photos", "whatsapp", "promise", "missed_call", "payment", "stay_request", "request"]
TicketStatus = Literal["open", "done", "dismissed"]


class TicketOut(BaseModel):
    id: uuid.UUID
    kind: str
    summary: str
    detail: str | None
    urgency: str
    status: str
    source: str
    created_at: datetime
    resolved_at: datetime | None
    property_id: uuid.UUID | None
    property_name: str | None
    call_session_id: uuid.UUID | None
    guest_name: str | None
    guest_phone: str | None
    # The alert raised with it, when there is one.
    notification_id: uuid.UUID | None = None
    # Set when the guest has a booking (Leads -> Checked-in): that booking's lead, and where the stay stands.
    booking_lead_id: uuid.UUID | None = None
    booking_phase: Literal["in_stay", "upcoming", "checked_out"] | None = None


class TicketUpdate(BaseModel):
    status: TicketStatus
