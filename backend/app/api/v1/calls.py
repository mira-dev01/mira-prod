import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.common import DateRange, date_range_query
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.notification import Notification
from app.models.user import User
from app.schemas.call_session import CallSessionDetailOut, CallSessionOut
from app.schemas.notification import NotificationOut
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("", response_model=list[CallSessionOut])
async def list_calls(
    status_filter: str | None = Query(default=None, alias="status"),
    urgency: str | None = Query(default=None),
    call_type: str | None = Query(default=None, description="Comma-separated call_type values, e.g. 'JUNK' or 'BOOKING_LEAD,GENERAL_QUERY'"),
    limit: int = Query(default=100, le=500),
    include_test_calls: bool = Query(default=False),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CallSession]:
    # Scoped by user_id, not property ownership -- Lead Agent calls have no
    # single property (property_id is NULL) but still belong to a host.
    # lead/guest_profile are eager-loaded -- CallSessionOut's guest_name/
    # guest_phone read them, and the async session is closed by the time
    # Pydantic serializes the response, so a lazy load there would crash.
    stmt = (
        select(CallSession)
        .options(selectinload(CallSession.lead), selectinload(CallSession.guest_profile))
        .where(CallSession.user_id == current_user.id)
        .order_by(CallSession.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(CallSession.status == status_filter)
    if urgency:
        stmt = stmt.where(CallSession.urgency == urgency)
    if call_type:
        # Comma-separated so "Qualified Calls" (a grouping of several
        # call_type values, not a stored value itself) uses the exact same
        # filter path as every single-value tab -- no special-casing.
        types = [t.strip() for t in call_type.split(",") if t.strip()]
        if types:
            stmt = stmt.where(CallSession.call_type.in_(types))
    if not include_test_calls:
        stmt = stmt.where(CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)
    if date_range.since is not None:
        stmt = stmt.where(CallSession.created_at >= date_range.since)
    if date_range.until is not None:
        stmt = stmt.where(CallSession.created_at < date_range.until)
    return list((await db.scalars(stmt)).all())


@router.get("/{call_id}", response_model=CallSessionDetailOut)
async def get_call(
    call_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> CallSession:
    # notifications eager-loaded (with .property for NotificationOut's
    # computed property_name) since escalations is read off this object
    # after the session closes -- same pattern as lead/guest_profile above.
    call = await db.scalar(
        select(CallSession)
        .options(
            selectinload(CallSession.lead),
            selectinload(CallSession.guest_profile),
            selectinload(CallSession.notifications).selectinload(Notification.property),
        )
        .where(CallSession.id == call_id)
    )
    if call is None or call.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call session not found")
    return CallSessionDetailOut(
        **CallSessionOut.model_validate(call).model_dump(),
        escalations=[NotificationOut.model_validate(n) for n in call.notifications],
    )
