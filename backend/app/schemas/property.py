import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FAQItem(BaseModel):
    question: str
    answer: str


class SeasonalNote(BaseModel):
    note: str
    start_month: int = Field(ge=1, le=12)
    end_month: int = Field(ge=1, le=12)


class PropertyCreate(BaseModel):
    name: str
    city: str | None = None
    exophone: str | None = None
    base_price: float = Field(ge=0)
    ical_url: str | None = None
    usp: str | None = Field(default=None, max_length=280)
    house_rules: str | None = None
    neighborhood_info: str | None = None
    faq: list[FAQItem] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    seasonal_notes: list[SeasonalNote] = Field(default_factory=list)
    check_in_time: str = "14:00"
    check_out_time: str = "11:00"
    max_guests: int = 4

    @field_validator("exophone")
    @classmethod
    def _blank_exophone_is_none(cls, value: str | None) -> str | None:
        # exophone is unique in the DB -- an empty string is a real value
        # there (and would collide across properties), so treat "not set"
        # as None rather than "".
        return value or None


class PropertyUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    exophone: str | None = None
    base_price: float | None = None
    ical_url: str | None = None
    usp: str | None = Field(default=None, max_length=280)
    house_rules: str | None = None
    neighborhood_info: str | None = None
    faq: list[FAQItem] | None = None
    amenities: list[str] | None = None
    photos: list[str] | None = None
    seasonal_notes: list[SeasonalNote] | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    max_guests: int | None = None

    @field_validator("exophone")
    @classmethod
    def _blank_exophone_is_none(cls, value: str | None) -> str | None:
        return value or None


class PropertyGalleryOut(BaseModel):
    """Unauthenticated, minimal view for the public guest-facing photo
    gallery page -- only fields safe to expose with no login (see the
    send_photos voice tool in app/services/tool_handlers.py, which is the
    only thing that hands this URL out)."""

    id: uuid.UUID
    name: str
    city: str | None
    photos: list[str]


class PropertyOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    city: str | None
    exophone: str | None
    base_price: float
    ical_url: str | None
    usp: str | None
    house_rules: str | None
    neighborhood_info: str | None
    faq: list[dict]
    amenities: list[str]
    photos: list[str]
    seasonal_notes: list[SeasonalNote]
    check_in_time: str
    check_out_time: str
    max_guests: int
    airbnb_listing_id: str | None
    smart_price_estimate: float | None
    smart_price_sample_size: int
    smart_price_updated_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PropertyImportResult(BaseModel):
    filename: str
    status: Literal["created", "updated", "error"]
    property: PropertyOut | None = None
    faq_entries_created: int = 0
    error: str | None = None


class AirbnbUrlImportRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)


class AirbnbUrlImportTriggered(BaseModel):
    snapshot_id: str


class AirbnbUrlImportStatus(BaseModel):
    status: Literal["running", "ready", "failed"]
    results: list[PropertyImportResult] = Field(default_factory=list)
    error: str | None = None
