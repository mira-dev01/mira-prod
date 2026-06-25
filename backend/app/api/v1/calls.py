import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.user import User
from app.schemas.call_session import CallSessionOut

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("", response_model=list[CallSessionOut])
async def list_calls(
    status_filter: str | None = Query(default=None, alias="status"),
    urgency: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CallSession]:
    property_ids = await owned_property_ids(db, current_user)
    stmt = (
        select(CallSession)
        .where(CallSession.property_id.in_(property_ids))
        .order_by(CallSession.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(CallSession.status == status_filter)
    if urgency:
        stmt = stmt.where(CallSession.urgency == urgency)
    return list((await db.scalars(stmt)).all())


@router.get("/{call_id}", response_model=CallSessionOut)
async def get_call(
    call_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> CallSession:
    property_ids = await owned_property_ids(db, current_user)
    call = await db.get(CallSession, call_id)
    if call is None or call.property_id not in property_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call session not found")
    return call
