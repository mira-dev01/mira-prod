import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import AsyncSessionLocal, get_db
from app.models.user import User
from app.schemas.notification import NotificationOut
from app.services.notification_service import list_notifications, mark_read

router = APIRouter(prefix="/notifications", tags=["notifications"])

POLL_INTERVAL_SECONDS = 2.0


@router.get("", response_model=list[NotificationOut])
async def get_notifications(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    property_ids = await owned_property_ids(db, current_user)
    return await list_notifications(db, user_property_ids=property_ids, user_id=current_user.id)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def read_notification(notification_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    notification = await mark_read(db, notification_id)
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

    async def event_generator():
        last_seen = datetime.now(timezone.utc)

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

            yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
