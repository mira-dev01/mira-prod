import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# See app/models/booking.py and app/services/booking_reconciliation_service.py
# for what each value means. Validated here at the API boundary; the DB
# columns are plain strings, same convention as Lead.status.
BookingKind = Literal["reservation", "blocked"]
PriceSource = Literal["mira_conversation", "host_confirmed", "host_entered", "external_pms", "unknown"]
PriceStatus = Literal["confirmed", "pending_confirmation", "unknown"]
AttributionStatus = Literal["confirmed", "probable", "not_attributed", "unknown"]
PriceChoice = Literal["mira_conversation", "outside_mira", "not_confirmed_yet"]


class BookingCreate(BaseModel):
    property_id: uuid.UUID
    guest_phone: str | None = None
    guest_name: str | None = None
    check_in: date
    check_out: date
    platform: str = "manual"
    kind: BookingKind = "reservation"
    # The host knows the price at the moment they block the dates
    # themselves -- captured once here (price_source=host_entered) instead
    # of asked for later.
    final_booking_price: float | None = Field(default=None, gt=0)
    # Set when the booking is created by closing a Mira lead ("Confirm
    # booking") -- the host asserting the attribution.
    lead_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check_out_after_check_in(self) -> "BookingCreate":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class BookingOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    guest_phone: str | None
    guest_phone_last4: str | None = None
    guest_name: str | None
    check_in: date
    check_out: date
    platform: str
    status: str
    kind: str = "reservation"
    created_at: datetime
    initial_price: float | None = None
    negotiated_price: float | None = None
    final_booking_price: float | None = None
    currency: str = "INR"
    price_source: str = "unknown"
    price_status: str = "unknown"
    price_confirmed_at: datetime | None = None
    price_confirmed_by: str | None = None
    mira_attribution_status: str = "unknown"
    mira_attribution_call_session_id: uuid.UUID | None = None
    mira_attribution_lead_id: uuid.UUID | None = None
    mira_attribution_signals: list[str] = []
    # Derived (Booking model properties), so the frontend never re-derives
    # "does this need the host?" with its own copy of the rules.
    needs_price_confirmation: bool = False
    needs_attribution_review: bool = False
    detected_price: float | None = None

    model_config = {"from_attributes": True}


class AvailabilityQuery(BaseModel):
    property_id: uuid.UUID
    check_in: date
    check_out: date
    num_guests: int = Field(default=1, ge=1)


class BookingPriceConfirmation(BaseModel):
    choice: PriceChoice
    # Required for outside_mira. Optional for mira_conversation (defaults to
    # the detected price; the host may correct it). Ignored for
    # not_confirmed_yet.
    final_booking_price: float | None = Field(default=None, gt=0)


class BookingAttributionDecision(BaseModel):
    is_mira_match: bool


class BookingKindUpdate(BaseModel):
    kind: BookingKind


class MatchedConversationOut(BaseModel):
    lead_id: uuid.UUID
    call_session_id: uuid.UUID | None
    guest_name: str | None
    guest_phone: str | None
    lead_temperature: str | None
    conversation_at: datetime | None
    summary: str | None


class BookingReconciliationOut(BaseModel):
    """One booking with everything the host needs to answer for it -- used
    by both the Needs-confirmation queue and the Calendar booking panel."""

    booking: BookingOut
    property_name: str
    conversation: MatchedConversationOut | None


class ReconciliationQueueOut(BaseModel):
    items: list[BookingReconciliationOut]
    total: int
    price_missing: int
    attribution_pending: int
