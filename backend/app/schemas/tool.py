"""Pydantic argument models for the LLM tool functions, matching the
parameter names/types app/voice/tools.py's DirectFunction wrappers expose to
the LLM, so the wrappers and our handlers in app/services/tool_handlers.py
stay in lockstep.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

Urgency = Literal["low", "medium", "high", "emergency"]
IssueType = Literal["plumbing", "electrical", "ac", "wifi", "lock", "general"]
GuestLoyalty = Literal["new", "returning", "frequent"]
LeadTemperature = Literal["hot", "warm", "cold"]


class CheckCalendarArgs(BaseModel):
    property_id: str
    check_in: date
    check_out: date
    num_guests: int | None = None


class GetPricingArgs(BaseModel):
    property_id: str
    check_in: date
    check_out: date
    num_guests: int
    apply_discounts: bool = True


class DispatchTechnicianArgs(BaseModel):
    property_id: str
    issue_type: IssueType
    urgency: Urgency
    guest_phone: str | None = None


class SendWhatsappArgs(BaseModel):
    phone: str
    message: str
    template_name: str | None = None


class EscalateToHostArgs(BaseModel):
    property_id: str
    reason: str
    urgency: Urgency
    guest_phone: str | None = None
    call_summary: str | None = None


class NegotiateRateArgs(BaseModel):
    property_id: str
    check_in: date
    check_out: date
    guest_offer: float | None = None
    num_guests: int | None = None
    guest_loyalty: GuestLoyalty = "new"


class RecommendPropertiesArgs(BaseModel):
    budget: float | None = None
    num_guests: int | None = None
    preferred_location: str | None = None
    purpose_of_stay: str | None = None


class UpdateLeadArgs(BaseModel):
    guest_name: str | None = None
    phone: str | None = None
    email: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    num_guests: int | None = None
    purpose_of_stay: str | None = None
    budget: float | None = None
    preferred_location: str | None = None
    lead_temperature: LeadTemperature | None = None
    properties_discussed: list[str] | None = None
    questions_asked: list[str] | None = None
    support_requests: list[str] | None = None
    conversation_summary: str | None = None
    next_follow_up: str | None = None
    escalated: bool | None = None
    transferred_to_host: bool | None = None


class SearchFaqArgs(BaseModel):
    query: str
    property_id: str | None = None
