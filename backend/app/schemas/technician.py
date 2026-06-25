import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Specialty = Literal["plumbing", "electrical", "ac", "wifi", "lock", "general"]


class TechnicianCreate(BaseModel):
    property_id: uuid.UUID
    name: str
    specialty: Specialty
    phone: str
    rating: float = 5.0


class TechnicianOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    name: str
    specialty: str
    phone: str
    rating: float
    created_at: datetime

    model_config = {"from_attributes": True}
