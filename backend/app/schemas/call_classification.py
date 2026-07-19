"""Types for the end-of-call classification taxonomy -- see
app/services/call_classification_service.py for the classifier itself.
Split out from schemas/call_session.py since CallType/QUALIFIED_CALL_TYPES
are also needed by API filters (app/api/v1/calls.py) and analytics
(app/api/v1/analytics.py), not just the call_session response shape.
"""

from typing import Literal

from pydantic import BaseModel

CallType = Literal[
    "BOOKING_LEAD",
    "GUEST_SUPPORT",
    "EXISTING_BOOKING",
    "GENERAL_QUERY",
    "JUNK",
    "INCOMPLETE",
    "UNKNOWN",
]

# "Qualified" is deliberately never a stored value (see call_session.call_type's
# comment) -- it's this derived grouping, computed wherever needed, so there's
# never a second source of truth to drift out of sync with call_type itself.
QUALIFIED_CALL_TYPES: set[CallType] = {"BOOKING_LEAD", "GUEST_SUPPORT", "EXISTING_BOOKING", "GENERAL_QUERY"}


class ClassificationResult(BaseModel):
    call_type: CallType
    confidence: float
    reason: str
