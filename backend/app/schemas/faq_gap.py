import uuid
from datetime import datetime

from pydantic import BaseModel


class FaqGapOut(BaseModel):
    sample_id: uuid.UUID
    question: str
    count: int
    property_id: uuid.UUID | None
    last_asked_at: datetime


class FaqGapAnswerIn(BaseModel):
    answer: str
    apply_to_property: bool = False


class FaqGapAnalyticsOut(BaseModel):
    most_frequent: list[dict]
    by_property: list[dict]
    over_time: list[dict]
