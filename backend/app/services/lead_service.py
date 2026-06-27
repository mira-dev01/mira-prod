"""CRM records for the Lead Agent flow. update_lead (the voice tool) calls
upsert_lead repeatedly during a single call as the agent learns more about
the guest; the dashboard's Leads page reads back through list_leads/get_lead.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def list_leads(db: AsyncSession, user_id: uuid.UUID) -> list[Lead]:
    return list(
        (await db.scalars(select(Lead).where(Lead.user_id == user_id).order_by(Lead.created_at.desc()))).all()
    )


async def get_owned_lead(db: AsyncSession, lead_id: uuid.UUID, user_id: uuid.UUID) -> Lead | None:
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.user_id != user_id:
        return None
    return lead
