"""CRM records for the Lead Agent flow. update_lead (the voice tool) calls
upsert_lead repeatedly during a single call as the agent learns more about
the guest; the dashboard's Leads page reads back through list_leads/get_lead.
"""

import uuid

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
