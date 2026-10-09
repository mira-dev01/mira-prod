import logging
import uuid
from typing import Literal
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property, owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User
from app.schemas.booking import (
    AvailabilityQuery,
    BookingAttributionDecision,
    BookingCreate,
    BookingKindUpdate,
    BookingOut,
    BookingPriceConfirmation,
    BookingReconciliationOut,
    ReconciliationQueueOut,
)
from app.services import booking_reconciliation_service as reconciliation
from app.services.calendar_service import is_available

logger = logging.getLogger(__name__)

QUEUE_PAGE_SIZE = 50

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
    property_ = await get_owned_property(db, payload.property_id, current_user)
    data = payload.model_dump(exclude={"final_booking_price", "lead_id"})
    booking = Booking(**data)
    if payload.final_booking_price is not None:
        now = datetime.now(timezone.utc)
        booking.final_booking_price = payload.final_booking_price
        booking.price_source = "host_entered"
        booking.price_status = "confirmed"
        booking.price_confirmed_by = "host"
        booking.price_confirmed_at = booking.price_reviewed_at = now
    db.add(booking)
    await db.flush()

    lead = None
    if payload.lead_id is not None:
        lead = await db.get(Lead, payload.lead_id)
        if lead is None or lead.user_id != current_user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    # Built before the savepoint: a rolled-back savepoint expires `booking`,
    # and reading an expired attribute afterwards would lazy-load (fails
    # under asyncio).
    host_price_event = (
        reconciliation.final_price_event(booking, current_user.id, "host_entered")
        if payload.final_booking_price is not None
        else None
    )
    # Best-effort, same as the iCal sync: a reconciliation failure leaves a
    # plain booking behind rather than failing the host's request.
    booking_id = booking.id
    try:
        async with db.begin_nested():
            if lead is not None:
                await reconciliation.attach_host_lead(db, booking, lead, property_)
            else:
                await reconciliation.reconcile_booking(db, booking, property_)
    except Exception:  # noqa: BLE001
        logger.exception("Booking reconciliation failed for booking %s", booking_id)
    if host_price_event is not None:
        db.add(host_price_event)
    await db.commit()
    await db.refresh(booking)
    return booking


async def _get_owned_booking(db: AsyncSession, booking_id: uuid.UUID, user: User) -> tuple[Booking, Property]:
    booking = await db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    property_ = await get_owned_property(db, booking.property_id, user)
    return booking, property_


async def _reconciliation_view(db: AsyncSession, booking: Booking, property_name: str) -> BookingReconciliationOut:
    conversation = None
    if booking.mira_attribution_lead_id is not None:
        lead = await db.get(Lead, booking.mira_attribution_lead_id)
        call = (
            await db.get(CallSession, booking.mira_attribution_call_session_id)
            if booking.mira_attribution_call_session_id
            else None
        )
        if lead is not None:
            call_summary = (call.ai_summary or {}).get("conversation_summary") if call is not None else None
            conversation = {
                "lead_id": lead.id,
                "call_session_id": call.id if call is not None else None,
                "guest_name": lead.guest_name,
                "guest_phone": lead.phone or (call.caller_number if call is not None else None),
                "lead_temperature": lead.lead_temperature,
                "conversation_at": (call.started_at or call.created_at) if call is not None else lead.updated_at,
                "summary": lead.conversation_summary or call_summary,
            }
    return BookingReconciliationOut(
        booking=BookingOut.model_validate(booking), property_name=property_name, conversation=conversation
    )


@router.get("/reconciliation", response_model=ReconciliationQueueOut)
async def reconciliation_queue(
    kind: Literal["price", "attribution"] | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationQueueOut:
    """The "Booking details need confirmation" queue: bookings missing a
    final price the host hasn't answered for, and probable Mira matches
    awaiting a Yes/No."""
    properties = {
        p.id: p.name for p in (await db.scalars(select(Property).where(Property.user_id == current_user.id))).all()
    }
    rows = await reconciliation.reconciliation_queue(db, list(properties), kind)
    items = [await _reconciliation_view(db, booking, properties[booking.property_id]) for booking in rows[:QUEUE_PAGE_SIZE]]
    return ReconciliationQueueOut(
        items=items,
        total=len(rows),
        price_missing=sum(1 for b in rows if b.needs_price_confirmation),
        attribution_pending=sum(1 for b in rows if b.needs_attribution_review),
    )


@router.get("/{booking_id}/reconciliation", response_model=BookingReconciliationOut)
async def booking_reconciliation(
    booking_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> BookingReconciliationOut:
    booking, property_ = await _get_owned_booking(db, booking_id, current_user)
    return await _reconciliation_view(db, booking, property_.name)


@router.patch("/{booking_id}/price", response_model=BookingReconciliationOut)
async def confirm_booking_price(
    booking_id: uuid.UUID,
    payload: BookingPriceConfirmation,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BookingReconciliationOut:
    booking, property_ = await _get_owned_booking(db, booking_id, current_user)
    if not booking.is_reservation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Blocked dates have no booking price")
    try:
        booking = await reconciliation.confirm_price(
            db, booking, current_user.id, payload.choice, payload.final_booking_price
        )
    except reconciliation.ReconciliationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await _reconciliation_view(db, booking, property_.name)


@router.patch("/{booking_id}/attribution", response_model=BookingReconciliationOut)
async def decide_booking_attribution(
    booking_id: uuid.UUID,
    payload: BookingAttributionDecision,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BookingReconciliationOut:
    booking, property_ = await _get_owned_booking(db, booking_id, current_user)
    try:
        booking = await reconciliation.decide_attribution(db, booking, property_, payload.is_mira_match)
    except reconciliation.ReconciliationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await _reconciliation_view(db, booking, property_.name)


@router.patch("/{booking_id}/kind", response_model=BookingReconciliationOut)
async def update_booking_kind(
    booking_id: uuid.UUID,
    payload: BookingKindUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BookingReconciliationOut:
    """Host correction of reservation vs. blocked dates (e.g. an iCal block
    that was really a guest stay). Doesn't touch availability."""
    booking, property_ = await _get_owned_booking(db, booking_id, current_user)
    booking.kind = payload.kind
    if payload.kind == "reservation" and booking.final_booking_price is None and booking.price_status == "unknown":
        booking.price_status = "pending_confirmation"
    await db.commit()
    await db.refresh(booking)
    return await _reconciliation_view(db, booking, property_.name)


@router.delete("/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_booking(
    booking_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    """Unblock dates -- a cancellation, a manual block entered by mistake,
    or any other reason the host wants the property bookable again. Works
    for both manual blocks and Airbnb-synced bookings: if a synced booking
    is unblocked here but is still active in Airbnb's feed, the next iCal
    sync will recreate it, which is correct -- this only actually "sticks"
    once it's also resolved on the source platform.
    """
    booking = await db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    await get_owned_property(db, booking.property_id, current_user)
    await db.delete(booking)
    await db.commit()


@router.post("/check-availability")
async def check_availability(
    payload: AvailabilityQuery, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    await get_owned_property(db, payload.property_id, current_user)
    available = await is_available(db, payload.property_id, payload.check_in, payload.check_out)
    return {"available": available}
