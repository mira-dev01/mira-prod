"""In-app notifications -- the dashboard's live Requests feed polls/streams
from this table. send_whatsapp/send_photos/escalate_to_host all write here
as a record of what was sent, alongside whatever real channel (Twilio
WhatsApp sandbox, SMTP) actually attempted delivery -- see
app/integrations/twilio_client.py and email_client.py. A real WhatsApp
Business API (Meta-approved) number would replace the Twilio sandbox here
once the host has one; nothing in this table changes either way.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.lead import Lead
from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    channel: str,
    message: str,
    property_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    urgency: str = "low",
    lead_id: uuid.UUID | None = None,
) -> Notification:
    notification = Notification(
        property_id=property_id,
        call_session_id=call_session_id,
        lead_id=lead_id,
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
    user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[Notification]:
    # eager-load property -- Notification.property_name (NotificationOut's
    # source for it) reads this relationship, and the async session that
    # fetched these rows may already be closed by the time it's accessed
    # (see notification.py's property_name docstring).
    stmt = (
        select(Notification)
        .options(selectinload(Notification.property))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if user_property_ids is not None:
        # OR'd with a lead_id -> Lead.user_id match (principal-review fix):
        # a Lead Agent busy-recovery notification (portfolio-wide line, no
        # single property -- see recovery_service.py) always has
        # property_id=NULL, which a bare property_id.in_(...) filter can
        # never match -- that notification was being written correctly and
        # then silently excluded from every real consumption path (this
        # list, and /notifications/stream, which calls this same function),
        # permanently invisible on the dashboard even though the guest-
        # facing WhatsApp side of the same recovery worked fine. lead_id is
        # only ever set on recovery notifications (see Notification.lead_id's
        # own comment); user_id, when given, closes that gap without
        # touching the property_id path any other channel (whatsapp/
        # escalation/system) still relies on.
        property_match = Notification.property_id.in_(user_property_ids)
        if user_id is not None:
            owned_lead_ids = select(Lead.id).where(Lead.user_id == user_id).scalar_subquery()
            stmt = stmt.where(or_(property_match, Notification.lead_id.in_(owned_lead_ids)))
        else:
            stmt = stmt.where(property_match)
    if since is not None:
        stmt = stmt.where(Notification.created_at > since)
    return list((await db.scalars(stmt)).all())


async def mark_read(db: AsyncSession, notification_id: uuid.UUID) -> Notification | None:
    # db.get() doesn't take eager-load options, and async SQLAlchemy has no
    # implicit lazy-load -- refresh() with attribute_names populates
    # `property` after the update so NotificationOut's property_name can
    # read it without a MissingGreenlet error.
    notification = await db.get(Notification, notification_id)
    if notification is None:
        return None
    notification.status = "read"
    # First-read-wins: set once, never overwritten by a later mark_read call
    # on an already-read notification -- see Notification.responded_at's own
    # comment for why this exists separately from updated_at.
    if notification.responded_at is None:
        notification.responded_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(notification, attribute_names=["property"])
    return notification
