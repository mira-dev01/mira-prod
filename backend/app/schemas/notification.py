import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID | None
    call_session_id: uuid.UUID | None
    channel: str
    urgency: str
    message: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
