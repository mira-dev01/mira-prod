import ipaddress
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


def _validate_ical_url(value: str | None) -> str | None:
    """Blocks the obvious SSRF case: a host pointing ical_url at an internal/
    cloud-metadata address. fetch_ical (app/integrations/ical_client.py) has
    no destination checking of its own, and is re-run on every property
    automatically by the unconditional sync cron -- reject here, once, at
    the point the host actually sets the value, rather than at fetch time."""
    if not value:
        return value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValueError("ical_url must be an http:// or https:// URL")
    host = parts.hostname
    if not host:
        raise ValueError("ical_url must include a host")
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("ical_url may not point to a local/internal host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
        raise ValueError("ical_url may not point to a private/internal address")
    return value


class FAQItem(BaseModel):
    question: str
    answer: str


class SeasonalNote(BaseModel):
    note: str
    start_month: int = Field(ge=1, le=12)
    end_month: int = Field(ge=1, le=12)


class LandmarkItem(BaseModel):
    name: str
    distance_minutes: int = Field(ge=0)
    mode: Literal["walk", "drive"] | None = None


class PropertyCreate(BaseModel):
    name: str
    city: str | None = None
    exophone: str | None = None
    twilio_number: str | None = None
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
    minimum_nights: int = Field(default=1, ge=1)
    saturday_minimum_stay_enabled: bool = False

    @field_validator("exophone", "twilio_number")
    @classmethod
    def _blank_exophone_is_none(cls, value: str | None) -> str | None:
        # exophone/twilio_number are both unique in the DB -- an empty
        # string is a real value there (and would collide across
        # properties), so treat "not set" as None rather than "".
        return value or None

    @field_validator("ical_url")
    @classmethod
    def _check_ical_url(cls, value: str | None) -> str | None:
        return _validate_ical_url(value)


class PropertyUpdate(BaseModel):
    name: str | None = None
    display_name: str | None = Field(default=None, max_length=120)
    spoken_name: str | None = Field(default=None, max_length=60)
    property_type: str | None = Field(default=None, max_length=60)
    property_style: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    bedroom_count: int | None = Field(default=None, ge=0)
    city: str | None = None
    exophone: str | None = None
    twilio_number: str | None = None
    base_price: float | None = None
    ical_url: str | None = None
    usp: str | None = Field(default=None, max_length=280)
    house_rules: str | None = None
    neighborhood_info: str | None = None
    landmarks: list[LandmarkItem] | None = None
    faq: list[FAQItem] | None = None
    amenities: list[str] | None = None
    photos: list[str] | None = None
    seasonal_notes: list[SeasonalNote] | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    max_guests: int | None = None
    minimum_nights: int | None = Field(default=None, ge=1)
    saturday_minimum_stay_enabled: bool | None = None
    exact_airbnb_pricing: bool | None = None
    is_premium: bool | None = None

    @field_validator("exophone", "twilio_number")
    @classmethod
    def _blank_exophone_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("ical_url")
    @classmethod
    def _check_ical_url(cls, value: str | None) -> str | None:
        return _validate_ical_url(value)


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
    raw_name: str | None
    display_name: str | None
    spoken_name: str | None
    property_type: str | None
    property_style: str | None
    brand: str | None
    bedroom_count: int | None
    city: str | None
    exophone: str | None
    twilio_number: str | None
    base_price: float
    ical_url: str | None
    usp: str | None
    house_rules: str | None
    neighborhood_info: str | None
    landmarks: list[LandmarkItem]
    faq: list[dict]
    amenities: list[str]
    amenity_tags: list[str]
    photos: list[str]
    seasonal_notes: list[SeasonalNote]
    check_in_time: str
    check_out_time: str
    max_guests: int
    minimum_nights: int
    saturday_minimum_stay_enabled: bool
    airbnb_listing_id: str | None
    smart_price_estimate: float | None
    smart_price_sample_size: int
    smart_price_updated_at: datetime | None
    exact_airbnb_pricing: bool
    is_premium: bool
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
