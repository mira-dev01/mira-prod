import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

FaqStatus = Literal["pending", "verified"]


class FaqScopeIn(BaseModel):
    """Which properties an FAQ answer applies to: every property
    (apply_to_all) or an explicit, non-empty list."""

    apply_to_all: bool = False
    property_ids: list[uuid.UUID] = []


class FaqEntryCreate(BaseModel):
    property_id: uuid.UUID | None = None
    # When given, wins over property_id: one entry per listed property.
    property_ids: list[uuid.UUID] | None = None
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
    group_id: uuid.UUID | None = None
    question: str
    answer: str
    category: str | None
    status: str
    verified_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
