"""Guest Memory (memory-architecture-plan.md section 1) write path -- runs
once, fire-and-forget, after a call's Lead row has already been
backfilled/cleaned up (see app/voice/pipeline.py's on_pipeline_finished).

Deliberately does NOT call any LLM for summarization. Lead.conversation_summary
is already written by the voice agent itself during the call (via the
update_lead tool -- see GOLDEN_RULES/LEAD_AGENT_INSTRUCTIONS in
system_prompt.py), so this only aggregates that pre-existing text onto the
guest's cross-call profile. A call with no Lead row (nothing the agent
captured) or no conversation_summary simply adds nothing -- not a failure,
just nothing to record.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.models.guest_profile import GuestProfile

# Caps conversation_summaries so a very long-tenured repeat guest's profile
# doesn't grow unbounded -- only the most recent calls matter for informing
# tone/context on the next one.
MAX_CONVERSATION_SUMMARIES = 20


async def update_guest_memory_from_call(
    db: AsyncSession,
    call_session_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None,
    property_id: uuid.UUID | None,
    property_name: str | None,
) -> None:
    """property_id/property_name come from the pipeline's own closure state
    (the property this call was actually about), not re-derived from
    Lead.properties_discussed -- that field only stores human-readable
    names (see tool_handlers.py's _resolve_property_names), not ids, so it
    can't reliably round-trip to a Property row. A Lead Agent call with no
    property chosen passes both as None -- last_property_id then just
    isn't updated this call, which is correct (nothing to record)."""
    if call_session_id is None or guest_profile_id is None:
        return

    lead = await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))
    if lead is None:
        # Lead was never created, or was deleted by delete_if_empty for
        # having no data -- nothing meaningful happened this call.
        return

    guest = await db.get(GuestProfile, guest_profile_id)
    if guest is None:
        return

    # Link the lead back to this guest profile so status/lead_temperature/
    # occasion stay single-sourced on Lead rather than duplicated here (see
    # memory-architecture-plan.md section 1.1).
    if lead.guest_profile_id is None:
        lead.guest_profile_id = guest.id

    guest.total_stays = (guest.total_stays or 0) + 1
    guest.last_call_at = lead.updated_at
    if property_id is not None:
        guest.last_property_id = property_id

    if lead.conversation_summary:
        entry = {
            "call_session_id": str(call_session_id),
            "property_id": str(property_id) if property_id else None,
            "property_name": property_name,
            "date": lead.updated_at.isoformat() if lead.updated_at else None,
            "summary": lead.conversation_summary,
            "lead_temperature": lead.lead_temperature,
        }
        summaries = list(guest.conversation_summaries or [])
        summaries.append(entry)
        guest.conversation_summaries = summaries[-MAX_CONVERSATION_SUMMARIES:]
        guest.last_outcome = lead.lead_temperature
        if lead.next_follow_up:
            guest.last_follow_up = lead.next_follow_up

    await db.commit()
