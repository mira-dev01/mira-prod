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
    # Outcome labels below -- unlike the seven values above, these are never
    # produced by classify_call's LLM prompt. Each one is a deterministic
    # fact the pipeline/webhook code already knows at the moment the call
    # ends -- transcript content is irrelevant to which applies, so asking
    # an LLM to infer one would be strictly worse than the call site just
    # setting it directly. Added to this same Literal (rather than a new
    # column/field) because call_type is the taxonomy the Calls tab already
    # filters/badges by end-to-end (schemas, calls-table.tsx, the
    # CALL_LOG_FILTERS filter bar).
    #
    # Where each is written:
    #  UNRESPONSIVE          - on_pipeline_finished, EndFrame(reason=
    #                          "silent caller") from the silence watchdog.
    #  MISSED_AGENT_BUSY     - call_service.record_busy_rejected_call, from
    #                          both BUSY_RECOVERY branches in pipeline.py --
    #                          a call CallCoordinator rejected because the
    #                          host/property was already on a live call.
    #  MISSED_SYSTEM_FAILURE - _run_pipeline's except-block (a mid-call
    #                          crash) and call_service.reconcile_stuck_call_
    #                          sessions (the periodic sweep for rows a
    #                          finalize path abandoned at "in_progress").
    #  TRANSFERRED_TO_HOST   - on_pipeline_finished, EndFrame(reason=
    #                          "host_handoff") -- Mira handed the live call
    #                          to the host's phone via the Connect applet.
    #  TRANSFERRED_TO_HOST_MISSED - NOT WRITTEN YET. Needs the Exotel
    #                          Connect-leg StatusCallback (Phase 2) to know
    #                          the host's leg went unanswered/busy; until
    #                          then every handoff is TRANSFERRED_TO_HOST.
    #  ESCALATED_NO_TRANSFER - on_pipeline_finished, when this call has its
    #                          own channel="escalation" Notification (the
    #                          escalate_to_host tool fired) but the call
    #                          was not a live handoff.
    "UNRESPONSIVE",
    "MISSED_AGENT_BUSY",
    "MISSED_SYSTEM_FAILURE",
    "TRANSFERRED_TO_HOST",
    "TRANSFERRED_TO_HOST_MISSED",
    "ESCALATED_NO_TRANSFER",
]

# "Qualified" is deliberately never a stored value (see call_session.call_type's
# comment) -- it's this derived grouping, computed wherever needed, so there's
# never a second source of truth to drift out of sync with call_type itself.
QUALIFIED_CALL_TYPES: set[CallType] = {"BOOKING_LEAD", "GUEST_SUPPORT", "EXISTING_BOOKING", "GENERAL_QUERY"}

# The six outcome labels above, grouped for read sites (the Calls-tab "Missed"
# filter group, analytics) that want to reason about "this call never reached
# a normal qualified/junk/incomplete conclusion" as one set, the same way
# QUALIFIED_CALL_TYPES lets callers reason about "this call was a real lead"
# without hardcoding the 4-value list themselves.
OUTCOME_CALL_TYPES: set[CallType] = {
    "UNRESPONSIVE",
    "MISSED_AGENT_BUSY",
    "MISSED_SYSTEM_FAILURE",
    "TRANSFERRED_TO_HOST",
    "TRANSFERRED_TO_HOST_MISSED",
    "ESCALATED_NO_TRANSFER",
}


class ClassificationResult(BaseModel):
    call_type: CallType
    confidence: float
    reason: str
