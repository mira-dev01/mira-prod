"""Pydantic argument models for the LLM tool functions, matching the
parameter names/types app/voice/tools.py's DirectFunction wrappers expose to
the LLM, so the wrappers and our handlers in app/services/tool_handlers.py
stay in lockstep.
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

Urgency = Literal["low", "medium", "high", "emergency"]
IssueType = Literal["plumbing", "electrical", "ac", "wifi", "lock", "general"]
GuestLoyalty = Literal["new", "returning", "frequent"]
LeadTemperature = Literal["hot", "warm", "cold"]


def _normalize_phone(value: str) -> str:
    """Keep only digits, then the last 10.

    Confirmed live: a guest self-correcting mid-dictation ("93263... [pause]
    9326359081") gets captured as one turn (the whole point of the
    turn-detection extension in app/voice/turn_strategies.py -- it's meant
    to wait through exactly this pause instead of splitting it in two), but
    the model then has no instruction on which fragment is the real
    answer and just concatenates both: "932639326359081". The complete,
    corrected number is always the LAST digits spoken, never the first --
    a guest restarts by speaking the whole number again, not by resuming
    mid-digit. Taking the last 10 also incidentally strips a leading
    "91"/"+91" country code. Left permissive (no length/format rejection)
    since a guest can legitimately still be mid-dictation when this runs.
    """
    digits = re.sub(r"\D", "", value)
    if not digits:
        return value
    return digits[-10:] if len(digits) > 10 else digits


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
    # Phase 6 (Negotiation engine): only True if the GUEST explicitly asked
    # about early check-in / late checkout -- never set proactively just
    # because a property happens to have a fee configured for it. Quoting a
    # fee the guest never asked about would be exactly the kind of
    # unprompted upsell GOLDEN_RULES' pricing-order discipline already
    # forbids for discounts; the same "only when asked" rule applies here.
    requested_early_checkin: bool = False
    requested_late_checkout: bool = False


class DispatchTechnicianArgs(BaseModel):
    property_id: str
    issue_type: IssueType
    urgency: Urgency
    guest_phone: str | None = None

    @field_validator("guest_phone")
    @classmethod
    def _clean_guest_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value) if value else value


class SendWhatsappArgs(BaseModel):
    phone: str
    message: str
    template_name: str | None = None

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, value: str) -> str:
        return _normalize_phone(value)


class EscalateToHostArgs(BaseModel):
    property_id: str
    reason: str
    urgency: Urgency
    guest_phone: str | None = None
    call_summary: str | None = None

    @field_validator("guest_phone")
    @classmethod
    def _clean_guest_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value) if value else value


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
    required_amenities: list[str] | None = None
    near_landmark: str | None = None
    # Recommendation conversations ("Phase X"): a guest saying "something
    # cheaper"/"larger"/"more premium" is a RELATIVE instruction, not a new
    # absolute number -- the model signals which direction, never invents a
    # rupee/guest-count figure itself (that would be exactly the kind of
    # LLM-guessed fact this codebase's tool-arg schemas otherwise avoid).
    # cheaper_than_shown/larger_than_shown are turned into a real budget/
    # num_guests ceiling by ConversationState.resolve_cheaper_budget/
    # resolve_larger_num_guests (app/voice/conversation_state.py), derived
    # from ConversationState.recommendations_shown -- the same "hand it a
    # fact, don't make it guess" discipline _today_anchor() and
    # comparison_notes already use elsewhere. more_premium_than_shown has no
    # equivalent resolver -- it's a ranking signal, not a threshold, consumed
    # directly by filter_builder.apply_premium_boost. Only ever set True when
    # the guest actually asked for a change in that direction; never set
    # alongside an explicit budget/num_guests in the SAME call (that would
    # be a real, absolute value already -- no resolution needed).
    cheaper_than_shown: bool = False
    larger_than_shown: bool = False
    more_premium_than_shown: bool = False


class UpdateLeadArgs(BaseModel):
    guest_name: str | None = None
    phone: str | None = None
    email: str | None = None

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value) if value else value
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
    occasion: str | None = None


class SearchFaqArgs(BaseModel):
    query: str
    property_id: str | None = None


class SendPhotosArgs(BaseModel):
    property_id: str
    guest_phone: str

    @field_validator("guest_phone")
    @classmethod
    def _clean_guest_phone(cls, value: str) -> str:
        return _normalize_phone(value)
