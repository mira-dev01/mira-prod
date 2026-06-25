"""Pydantic argument models for the 6 LLM tool functions, matching the exact
parameter names/types from MIRA_Tech_Architecture_Spec.pdf so Vapi's function
definitions and our handlers stay in lockstep.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

Urgency = Literal["low", "medium", "high", "emergency"]
IssueType = Literal["plumbing", "electrical", "ac", "wifi", "lock", "general"]
GuestLoyalty = Literal["new", "returning", "frequent"]


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
    guest_offer: float
    num_guests: int | None = None
    guest_loyalty: GuestLoyalty = "new"


TOOL_ARG_MODELS = {
    "check_calendar": CheckCalendarArgs,
    "get_pricing": GetPricingArgs,
    "dispatch_technician": DispatchTechnicianArgs,
    "send_whatsapp": SendWhatsappArgs,
    "escalate_to_host": EscalateToHostArgs,
    "negotiate_rate": NegotiateRateArgs,
}
