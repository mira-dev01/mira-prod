import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property, owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.booking import Booking
from app.models.user import User
from app.schemas.booking import AvailabilityQuery, BookingCreate, BookingOut
from app.services.calendar_service import is_available

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.get("", response_model=list[BookingOut])
async def list_bookings(
    property_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Booking]:
    if property_id is not None:
        await get_owned_property(db, property_id, current_user)
        property_ids = [property_id]
    else:
        property_ids = await owned_property_ids(db, current_user)

    return list(
        (
            await db.scalars(
                select(Booking).where(Booking.property_id.in_(property_ids)).order_by(Booking.check_in)
            )
        ).all()
    )


@router.post("", response_model=BookingOut, status_code=201)
async def create_booking(
    payload: BookingCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Booking:
    await get_owned_property(db, payload.property_id, current_user)
    booking = Booking(**payload.model_dump())
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    return booking


@router.post("/check-availability")
async def check_availability(
    payload: AvailabilityQuery, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    await get_owned_property(db, payload.property_id, current_user)
    available = await is_available(db, payload.property_id, payload.check_in, payload.check_out)
    return {"available": available}
