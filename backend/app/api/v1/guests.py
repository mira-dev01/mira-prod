import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange, date_range_query
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.property import Property
from app.models.user import User
from app.schemas.guest_profile import (
    GuestProfileDetailOut,
    GuestProfileOut,
    GuestProfileUpdate,
    GuestRecentCall,
)

router = APIRouter(prefix="/guests", tags=["guests"])


@router.get("", response_model=list[GuestProfileOut])
async def list_guests(
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[GuestProfile]:
    # Scoped by user_id, not property ownership -- Lead Agent calls have no
    # single property (property_id is NULL) but still belong to a host.
    #
    # date_range filters the CallSession scan (not GuestProfile directly --
    # GuestProfile.created_at means "profile first created," a different
    # concept from "had a call in this range"), so this reads as "guests who
    # had a call in this range."
    stmt = select(CallSession.guest_profile_id).where(
        CallSession.user_id == current_user.id, CallSession.guest_profile_id.isnot(None)
    )
    if date_range.since is not None:
        stmt = stmt.where(CallSession.created_at >= date_range.since)
    if date_range.until is not None:
        stmt = stmt.where(CallSession.created_at < date_range.until)
    guest_ids = (await db.scalars(stmt.distinct())).all()
    if not guest_ids:
        return []
    return list((await db.scalars(select(GuestProfile).where(GuestProfile.id.in_(guest_ids)))).all())


@router.get("/{guest_id}", response_model=GuestProfileOut)
async def get_guest(
    guest_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GuestProfile:
    has_called = await db.scalar(
        select(CallSession.id).where(
            CallSession.guest_profile_id == guest_id, CallSession.user_id == current_user.id
        )
    )
    if has_called is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guest not found")

    guest = await db.get(GuestProfile, guest_id)
    if guest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guest not found")
    return guest


@router.get("/{guest_id}/detail", response_model=GuestProfileDetailOut)
async def get_guest_detail(
    guest_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GuestProfileDetailOut:
    # Reuses get_guest's ownership check (a guest is only visible to a host
    # if they share at least one CallSession) rather than duplicating it.
    guest = await get_guest(guest_id, current_user, db)

    # lifetime_revenue: SUM(CallSession.revenue_attributed) for this guest,
    # scoped to the requesting host only -- a guest_profile row is keyed by
    # phone number, not per-host, so without the user_id filter a guest who
    # called two different hosts on MIRA would leak the other host's
    # revenue into this total.
    lifetime_revenue = await db.scalar(
        select(func.coalesce(func.sum(CallSession.revenue_attributed), 0)).where(
            CallSession.guest_profile_id == guest_id, CallSession.user_id == current_user.id
        )
    )

    recent_calls_stmt = (
        select(CallSession, Property.name)
        .outerjoin(Property, Property.id == CallSession.property_id)
        .where(CallSession.guest_profile_id == guest_id, CallSession.user_id == current_user.id)
        .order_by(CallSession.created_at.desc())
        .limit(10)
    )
    rows = (await db.execute(recent_calls_stmt)).all()
    recent_calls = [
        GuestRecentCall(
            id=call.id,
            property_id=call.property_id,
            property_name=property_name,
            status=call.status,
            ai_summary=call.ai_summary,
            started_at=call.started_at,
        )
        for call, property_name in rows
    ]

    return GuestProfileDetailOut(
        **GuestProfileOut.model_validate(guest).model_dump(),
        lifetime_revenue=float(lifetime_revenue or 0),
        recent_calls=recent_calls,
    )


@router.patch("/{guest_id}", response_model=GuestProfileOut)
async def update_guest(
    guest_id: uuid.UUID,
    payload: GuestProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GuestProfile:
    guest = await get_guest(guest_id, current_user, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(guest, field, value)
    await db.commit()
    await db.refresh(guest)
    return guest
