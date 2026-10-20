import uuid
from datetime import datetime

<<<<<<< HEAD
from pydantic import BaseModel
=======
from pydantic import BaseModel, Field
>>>>>>> 368377a (20th October)


class FaqGapOut(BaseModel):
    sample_id: uuid.UUID
    question: str
    count: int
    property_id: uuid.UUID | None
    last_asked_at: datetime
    suggested_faq_entry_id: uuid.UUID | None = None
    suggested_answer: str | None = None
    match_score: float | None = None


class FaqGapAnswerIn(BaseModel):
    answer: str
    apply_to_property: bool = False
<<<<<<< HEAD
=======
    # The host's own wording for the question (defaults to the guest's).
    question: str | None = Field(default=None, max_length=500)
>>>>>>> 368377a (20th October)


class FaqGapAnalyticsOut(BaseModel):
    most_frequent: list[dict]
    by_property: list[dict]
    over_time: list[dict]
