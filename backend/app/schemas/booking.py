import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class BookingCreate(BaseModel):
    property_id: uuid.UUID
    guest_phone: str | None = None
    guest_name: str | None = None
    check_in: date
    check_out: date
    platform: str = "manual"


class BookingOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    guest_phone: str | None
    guest_name: str | None
    check_in: date
    check_out: date
    platform: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AvailabilityQuery(BaseModel):
    property_id: uuid.UUID
    check_in: date
    check_out: date
    num_guests: int = Field(default=1, ge=1)
