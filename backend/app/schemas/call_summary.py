"""Structured, host-facing end-of-call summary -- see
app/services/call_summary_service.py for the generator. Stored as JSONB on
CallSession.ai_summary (app/models/call_session.py) so the dashboard can
render distinct sections instead of a single paragraph.
"""

from typing import Literal, get_args

from pydantic import BaseModel

# Controlled vocabulary for CallSummary.objection_tags (docs/tasks/
# building-intelligence.md, Implementation 2) -- deliberately a fixed list,
# not free text: see CLAUDE.md's "substring match against a free-text field"
# lesson for why an open-ended tag would be unreliable to aggregate over.
# call_summary_service._parse_summary_response enforces this vocabulary in
# code (set-membership filter), not just via the prompt -- an LLM ignoring
# the prompt's own instructions must never leak an unlisted tag into
# storage/analytics.
ObjectionTag = Literal[
    "PRICE_TOO_HIGH",
    "DATES_UNAVAILABLE",
    "LOCATION_MISMATCH",
    "AMENITY_MISSING",
    "POLICY_MISMATCH",
    "HOST_UNRESPONSIVE",
    "GUEST_STOPPED_RESPONDING",
    "NO_OBJECTION",
]
OBJECTION_TAGS: frozenset[str] = frozenset(get_args(ObjectionTag))


class BookingSnapshot(BaseModel):
    guest_name: str = "Unknown"
    intent: str = "Other"
    check_in: str = "Not provided"
    check_out: str = "Not provided"
    nights: str = "Unknown"
    guests: str = "Unknown"
    property: list[str] = []
    budget: str = "Not mentioned"
    occasion: str = "Not mentioned"
    language: str = "Not mentioned"
    # Guest-stated room/unit number for an in-stay service ask (e.g. "room
    # 204, can you send someone with water bottles") -- surfaced on the
    # Service Requests tab (frontend/src/app/dashboard/leads/page.tsx) since
    # no room-number field exists anywhere else in the data model (Property
    # is one listing per row, no multi-unit concept).
    room_number: str = "Not mentioned"


class SummaryOutcome(BaseModel):
    status: str = "No Booking"
    reason: str = "Not enough information to determine an outcome."


class CallSummary(BaseModel):
    booking_snapshot: BookingSnapshot
    conversation_summary: str
    outcome: SummaryOutcome
    host_action: list[str] = []
    key_details: list[str] = []
    missing_information: list[str] = []
    # Zero or more tags from OBJECTION_TAGS describing what friction, if any,
    # caused this call not to convert -- raw material for
    # objection_conversion_analytics (Implementation 4). "NO_OBJECTION" is an
    # explicit value (not just an empty list) so a genuinely smooth call is
    # distinguishable from "the model didn't extract anything."
    objection_tags: list[str] = []
