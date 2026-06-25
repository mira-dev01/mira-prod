import uuid
from datetime import datetime

from pydantic import BaseModel


class GuestProfileOut(BaseModel):
    id: uuid.UUID
    phone: str
    name: str | None
    total_stays: int
    preferences: dict
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class GuestProfileUpdate(BaseModel):
    name: str | None = None
    preferences: dict | None = None
    notes: str | None = None
