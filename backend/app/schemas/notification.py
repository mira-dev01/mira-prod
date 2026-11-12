import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID | None
    property_name: str | None
    call_session_id: uuid.UUID | None
    channel: str
    urgency: str
    message: str
    status: str
    created_at: datetime
    # Set for a request from a guest with an active booking (see Notification.booking_lead_id): the dashboard
    # shows a banner and counts it on the Active bookings tab.
    booking_lead_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}
