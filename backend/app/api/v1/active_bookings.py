"""Leads -> Active bookings: the booked guests (upcoming and in stay), their payment status, and the number of
requests from them the host has not seen yet. See app/services/active_booking_service.py."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import active_booking_service

router = APIRouter(prefix="/bookings/active", tags=["active-bookings"])


@router.get("")
async def list_active_bookings(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await active_booking_service.list_active(db, current_user)


@router.get("/activity-count")
async def activity_count(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """The number on the Active bookings tab and the sidebar: requests from booked guests not yet seen."""
    return {"count": await active_booking_service.unread_count(db, current_user.id)}


@router.post("/{lead_id}/activity/read")
async def read_activity(lead_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """The host has opened this guest: their requests stop counting."""
    return {"cleared": await active_booking_service.mark_activity_read(db, current_user.id, lead_id)}
