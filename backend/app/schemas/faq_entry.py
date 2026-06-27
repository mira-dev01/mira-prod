import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FaqStatus = Literal["pending", "verified"]


class FaqEntryCreate(BaseModel):
    property_id: uuid.UUID | None = None
    question: str
    answer: str
    category: str | None = None
    status: FaqStatus = "pending"
    verified_by: str | None = None


class FaqEntryUpdate(BaseModel):
    property_id: uuid.UUID | None = None
    question: str | None = None
    answer: str | None = None
    category: str | None = None
    status: FaqStatus | None = None
    verified_by: str | None = None


class FaqEntryOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    property_id: uuid.UUID | None
    question: str
    answer: str
    category: str | None
    status: str
    verified_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
