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


async def delete_if_empty(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Remove the auto-created Lead row for a call that never actually had
    any content (e.g. a connection that dropped instantly -- a reconnect
    blip right after a real call ends, or a mic-permission failure). Every
    call gets a Lead row the moment it starts, before any conversation
    happens, so a genuinely empty call leaves an empty Lead behind unless
    cleaned up -- which looks like a duplicate/phantom entry on the Leads
    page even though it's actually a separate, just-empty call.
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
