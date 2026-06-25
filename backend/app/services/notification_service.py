"""In-app notifications. Stands in for the WhatsApp Business API (which the
host doesn't have approved yet): the send_whatsapp and escalate_to_host tools
both write here instead of calling Meta's API, and the dashboard's live
Requests feed polls/streams from this table.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    channel: str,
    message: str,
    property_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    urgency: str = "low",
) -> Notification:
    notification = Notification(
        property_id=property_id,
        call_session_id=call_session_id,
        channel=channel,
        urgency=urgency,
        message=message,
        status="new",
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def list_notifications(
    db: AsyncSession,
    user_property_ids: list[uuid.UUID] | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if user_property_ids is not None:
        stmt = stmt.where(Notification.property_id.in_(user_property_ids))
    if since is not None:
        stmt = stmt.where(Notification.created_at > since)
    return list((await db.scalars(stmt)).all())


async def mark_read(db: AsyncSession, notification_id: uuid.UUID) -> Notification | None:
    notification = await db.get(Notification, notification_id)
    if notification is None:
        return None
    notification.status = "read"
    await db.commit()
    await db.refresh(notification)
    return notification
