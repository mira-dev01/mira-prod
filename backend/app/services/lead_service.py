"""CRM records for the Lead Agent flow. update_lead (the voice tool) calls
upsert_lead repeatedly during a single call as the agent learns more about
the guest; the dashboard's Leads page reads back through list_leads/get_lead.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange
from app.models.lead import Lead


async def upsert_lead(
    db: AsyncSession,
    user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    **fields,
) -> Lead:
    lead = None
    if call_session_id is not None:
        lead = await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))

    if lead is None:
        lead = Lead(user_id=user_id, call_session_id=call_session_id)
        db.add(lead)

    for key, value in fields.items():
        if value is not None:
            setattr(lead, key, value)

    await db.commit()
    await db.refresh(lead)
    return lead


async def backfill_lead_from_engagement(
    db: AsyncSession,
    user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    property_name: str,
    check_in: date,
    check_out: date,
    num_guests: int | None,
) -> None:
    """System-level safety net: creates/backfills a Lead the moment a guest
    engages meaningfully with a specific property + dates (get_pricing,
    negotiate_rate, check_calendar), independent of the LLM ever calling
    update_lead itself. Traced live: real booking calls were going through a
    full price negotiation and ending with zero Lead row, because the model
    said its escalation/booking phrases without reliably following through
    with the actual update_lead/escalate_to_host tool call -- a live LLM
    function-calling reliability gap, not a prompt clarity one. This makes a
    Lead's existence not depend on that call happening at all.

    Deliberately narrow to only what these three tool calls always carry
    (property, dates, guest count) -- never overwrites a field the guest/LLM
    already set via update_lead (same blank-only semantics as backfill_lead
    above), and never sets guest_name/phone/email/lead_temperature, which
    only mean something if actually given by the guest.
    """
    if call_session_id is None:
        return
    lead = await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))
    if lead is None:
        lead = Lead(user_id=user_id, call_session_id=call_session_id)
        db.add(lead)

    changed = False
    if property_name not in (lead.properties_discussed or []):
        lead.properties_discussed = [*(lead.properties_discussed or []), property_name]
        changed = True
    if not lead.check_in:
        lead.check_in = check_in
        changed = True
    if not lead.check_out:
        lead.check_out = check_out
        changed = True
    if num_guests and not lead.num_guests:
        lead.num_guests = num_guests
        changed = True

    if changed:
        await db.commit()


async def backfill_lead(db: AsyncSession, call_session_id: uuid.UUID | None, **fields) -> None:
    """Fill only currently-blank fields on an EXISTING lead at call end
    (e.g. the caller's phone from Exotel, or the property a Guest Support
    call was about). Never creates a lead: a call where the agent captured
    nothing must leave no row rather than an empty 'unknown guest' phantom,
    and anything the guest actually stated during the call (via update_lead)
    is authoritative and must not be overwritten here.
    """
    if call_session_id is None:
        return
    lead = await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))
    if lead is None:
        return
    changed = False
    for key, value in fields.items():
        # `not getattr(...)` treats None and the default empty list/"" as
        # blank -- so an already-populated field (a phone the guest gave, a
        # non-empty properties_discussed) is left untouched.
        if value and not getattr(lead, key, None):
            setattr(lead, key, value)
            changed = True
    if changed:
        await db.commit()


async def delete_if_empty(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Safety net for a lead that got created by a stray tool call on a call
    that never actually became a conversation (e.g. escalate_to_host firing
    on a connection that dropped instantly). Leads are no longer created up
    front (see app/voice/pipeline.py), so in the normal case there's nothing
    to clean; this just guards against a near-empty row slipping through and
    looking like a phantom entry on the Leads page.
    """
    if call_session_id is None:
        return
    lead = await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))
    if lead is None:
        return
    has_data = any(
        getattr(lead, field) for field in ("guest_name", "phone", "email", "check_in", "lead_temperature")
    )
    if not has_data:
        await db.delete(lead)
        await db.commit()


async def delete_for_unqualified_call(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Called from on_pipeline_finished after end-of-call classification
    (app/services/call_classification_service.py), for any call_type NOT in
    QUALIFIED_CALL_TYPES (JUNK/INCOMPLETE/UNKNOWN). Deletes the Lead row if
    one exists, regardless of how much data it has or whether
    escalate_to_host/update_lead ran mid-call -- unlike delete_if_empty
    (which only clears near-empty phantom rows), a junk/incomplete
    classification overrides whatever the live tool calls captured, since
    the full-transcript end-of-call review is necessarily more informed
    than the LLM's real-time in-call judgment. No-op if no Lead exists.
    """
    if call_session_id is None:
        return
    lead = await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))
    if lead is None:
        return
    await db.delete(lead)
    await db.commit()


async def list_leads(db: AsyncSession, user_id: uuid.UUID, date_range: DateRange | None = None) -> list[Lead]:
    stmt = select(Lead).where(Lead.user_id == user_id).order_by(Lead.created_at.desc())
    if date_range is not None:
        if date_range.since is not None:
            stmt = stmt.where(Lead.created_at >= date_range.since)
        if date_range.until is not None:
            stmt = stmt.where(Lead.created_at < date_range.until)
    return list((await db.scalars(stmt)).all())


async def get_owned_lead(db: AsyncSession, lead_id: uuid.UUID, user_id: uuid.UUID) -> Lead | None:
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.user_id != user_id:
        return None
    return lead


async def get_active_booking(db: AsyncSession, guest_profile_id: uuid.UUID | None, host_id: uuid.UUID) -> Lead | None:
    """Most recent Lead the host has marked status="booked" for this guest
    (Lead.status is host-managed from the Leads page, see app/models/lead.py)
    that isn't a past stay -- feeds system_prompt.py's guest-memory section
    so a returning guest calling about their upcoming/current stay gets
    recognized by name, property, and dates rather than treated as a fresh
    caller. check_out is None-or-future: a lead can be marked booked before
    the guest ever gave exact dates, and that's still worth surfacing.
    Returns None if there's no resolvable guest profile or no such lead --
    never raises, this is purely additive context for the prompt."""
    if guest_profile_id is None:
        return None
    stmt = (
        select(Lead)
        .where(
            Lead.guest_profile_id == guest_profile_id,
            Lead.user_id == host_id,
            Lead.status == "booked",
        )
        .order_by(Lead.updated_at.desc())
    )
    leads = (await db.scalars(stmt)).all()
    today = date.today()
    for lead in leads:
        if lead.check_out is None or lead.check_out >= today:
            return lead
    return None
