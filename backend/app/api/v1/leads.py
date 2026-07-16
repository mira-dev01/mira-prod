import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange, date_range_query
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.user import User
from app.schemas.lead import LeadOut, LeadUpdate
from app.services import lead_service

router = APIRouter(prefix="/leads", tags=["leads"])


async def _with_urgency(db: AsyncSession, leads: list[Lead]) -> list[LeadOut]:
    # Additive, read-only enrichment -- does not touch lead_service or the
    # Lead model (both are on the voice-agent write path and off-limits per
    # restructure.md standing rule 2). One query for the whole batch: latest
    # Notification.urgency per call_session_id, keyed by created_at so a
    # lead with several escalations over time shows its most recent one.
    call_session_ids = [lead.call_session_id for lead in leads if lead.call_session_id is not None]
    urgency_by_call_session: dict[uuid.UUID, str] = {}
    if call_session_ids:
        rows = (
            await db.execute(
                select(Notification.call_session_id, Notification.urgency, Notification.created_at)
                .where(Notification.call_session_id.in_(call_session_ids))
                .order_by(Notification.created_at.asc())
            )
        ).all()
        # asc order + dict overwrite -- last write per call_session_id wins,
        # i.e. the most recent notification's urgency.
        for call_session_id, urgency, _created_at in rows:
            urgency_by_call_session[call_session_id] = urgency

    return [
        LeadOut(
            **{k: getattr(lead, k) for k in LeadOut.model_fields if k != "urgency"},
            urgency=urgency_by_call_session.get(lead.call_session_id) if lead.call_session_id else None,
        )
        for lead in leads
    ]


@router.get("", response_model=list[LeadOut])
async def list_leads(
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LeadOut]:
    leads = await lead_service.list_leads(db, current_user.id, date_range)
    return await _with_urgency(db, leads)


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> LeadOut:
    lead = await lead_service.get_owned_lead(db, lead_id, current_user.id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return (await _with_urgency(db, [lead]))[0]


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    lead = await lead_service.get_owned_lead(db, lead_id, current_user.id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(lead, field, value)

    await db.commit()
    await db.refresh(lead)
    return (await _with_urgency(db, [lead]))[0]
