"""The Service Requests tab (frontend/src/app/dashboard/leads/page.tsx,
"Live Requests") -- one row per CallSession classified GUEST_SUPPORT by
call_classification_service (see schemas/call_classification.py). This is a
read-side view, not a new table: request text/urgency come from any linked
Notification (dispatch_technician/escalate_to_host/send_whatsapp/
send_photos writes -- see services/tool_handlers.py), falling back to the
call's own AI summary when no tool fired. See services/request_feed_service.py
for the query.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class ServiceRequestOut(BaseModel):
    call_session_id: uuid.UUID
    property_id: uuid.UUID | None
    property_name: str | None
    room_number: str | None
    message: str
    urgency: str
    created_at: datetime
    dismissed_at: datetime | None

    model_config = {"from_attributes": True}
