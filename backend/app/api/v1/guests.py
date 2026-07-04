import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange, date_range_query
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.user import User
from app.schemas.guest_profile import GuestProfileOut, GuestProfileUpdate

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
