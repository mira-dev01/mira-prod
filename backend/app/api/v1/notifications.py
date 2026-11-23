import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import AsyncSessionLocal, get_db
from app.models.user import User
from app.schemas.notification import NotificationOut
from app.schemas.ticket import TicketOut
from app.services import ticket_service
from app.services.notification_service import list_notifications, mark_read_owned

router = APIRouter(prefix="/notifications", tags=["notifications"])

POLL_INTERVAL_SECONDS = 2.0
# Tickets are matched by id inside a short window rather than "newer than the last one seen": a ticket's created_at is
# the start of the transaction that wrote it, so one that commits late can be older than one already sent.
TICKET_WINDOW = timedelta(minutes=2)


@router.get("", response_model=list[NotificationOut])
async def get_notifications(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    property_ids = await owned_property_ids(db, current_user)
    return await list_notifications(db, user_property_ids=property_ids, user_id=current_user.id)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def read_notification(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Authenticated + ownership-checked: a notification carries guest
    # details, so another host's id must be indistinguishable from a
    # nonexistent one (same 404).
    notification = await mark_read_owned(db, notification_id, current_user)
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    return notification


@router.get("/stream")
async def stream_notifications(
    request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Server-Sent Events feed powering the dashboard's live Requests view.
    Polls the DB on a short interval rather than using Postgres LISTEN/NOTIFY
    or a message broker -- simplest thing that works at Tier 1 call volume.
    """
    property_ids = await owned_property_ids(db, current_user)
    user_id = current_user.id

    async def event_generator():
        last_seen = datetime.now(timezone.utc)
        # What the host already has: tickets from just before they connected are not "new".
        async with AsyncSessionLocal() as stream_db:
            sent_tickets = {
                t["id"] for t in await ticket_service.list_tickets(stream_db, user_id, "all", since=last_seen - TICKET_WINDOW, limit=200)
            }

        while True:
            if await request.is_disconnected():
                break

            async with AsyncSessionLocal() as stream_db:
                new_notifications = await list_notifications(
                    stream_db, user_property_ids=property_ids, user_id=current_user.id, since=last_seen
                )

            for notification in reversed(new_notifications):
                last_seen = max(last_seen, notification.created_at)
                payload = NotificationOut.model_validate(notification).model_dump(mode="json")
                yield f"data: {json.dumps(payload)}\n\n"

            # Every new ticket is its own event, whether or not an alert was raised with it: a ticket is what the
            # host has to do, so each one must reach the dashboard (banner and ringer) on its own.
            async with AsyncSessionLocal() as stream_db:
                recent = await ticket_service.list_tickets(
                    stream_db, user_id, "all", since=datetime.now(timezone.utc) - TICKET_WINDOW, limit=200
                )
                new_tickets = [t for t in recent if t["id"] not in sent_tickets]
                if new_tickets:  # the booking lookup is several queries: only when there is something to mark
                    await ticket_service.attach_booking(stream_db, user_id, new_tickets)
            for ticket in reversed(new_tickets):
                sent_tickets.add(ticket["id"])
                payload = TicketOut.model_validate(ticket).model_dump(mode="json")
                yield f"event: ticket\ndata: {json.dumps(payload)}\n\n"

            yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
