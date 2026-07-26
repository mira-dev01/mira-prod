"""Read-side query backing the Service Requests tab (see
schemas/service_request.py). Purely additive/read-only -- does not touch
tool_handlers.py/lead_service.py, both on the voice-agent write path and
off-limits per restructure.md standing rule 2.

Classification uses CallSession.call_type (set once, end-of-call, by
call_classification_service -- see schemas/call_classification.py) rather
than "does a Lead or Notification row exist," since those two tables overlap
(escalate_to_host writes both) and both have gaps (a pure-FAQ call creates
neither). call_type is the one exhaustive, mutually-exclusive signal: every
qualifying call is GUEST_SUPPORT (-> Service Requests) xor
BOOKING_LEAD/EXISTING_BOOKING (-> Booking Requests / the Leads kanban), never
both, so a call can't be double-counted or dropped.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.call_session import CallSession
from app.models.notification import Notification
from app.schemas.service_request import ServiceRequestOut

SERVICE_REQUEST_CALL_TYPES = ("GUEST_SUPPORT",)


def _room_number(call: CallSession) -> str | None:
    if not call.ai_summary:
        return None
    room_number = (call.ai_summary.get("booking_snapshot") or {}).get("room_number")
    if not room_number or room_number == "Not mentioned":
        return None
    return room_number


def _property_name(call: CallSession) -> str | None:
    # A property-specific exophone call always has property_id set (see
    # app/voice/pipeline.py's _run_pipeline), so call.property.name is the
    # source of truth there. A Lead Agent (portfolio-wide) call has no single
    # property_id even when the guest was clearly asking about one specific
    # listing -- fall back to the AI summary's own extraction (see
    # schemas/call_summary.py's BookingSnapshot.property) so this column
    # isn't blank just because the call took the portfolio-wide number.
    if call.property_id and call.property:
        return call.property.name
    if call.ai_summary:
        discussed = (call.ai_summary.get("booking_snapshot") or {}).get("property")
        if discussed:
            return ", ".join(discussed)
    return None


def _message(call: CallSession, notifications: list[Notification]) -> str:
    # Prefer the most recent Notification's message (what dispatch_technician
    # /escalate_to_host/send_whatsapp/send_photos actually wrote) -- it's the
    # concrete action taken. Falls back to the AI summary's conversation
    # recap for a GUEST_SUPPORT call that only hit search_faq and never
    # triggered a tool, so it still shows up with something meaningful
    # instead of an empty row.
    if notifications:
        return max(notifications, key=lambda n: n.created_at).message
    if call.ai_summary and call.ai_summary.get("conversation_summary"):
        return call.ai_summary["conversation_summary"]
    return "Guest support call -- no summary available."


def _urgency(call: CallSession, notifications: list[Notification]) -> str:
    if notifications:
        return max(notifications, key=lambda n: n.created_at).urgency
    return call.urgency or "low"


def _to_out(call: CallSession) -> ServiceRequestOut:
    notifications = list(call.notifications)
    return ServiceRequestOut(
        call_session_id=call.id,
        property_id=call.property_id,
        property_name=_property_name(call),
        room_number=_room_number(call),
        message=_message(call, notifications),
        urgency=_urgency(call, notifications),
        created_at=call.created_at,
        dismissed_at=call.dismissed_at,
    )


async def list_service_requests(
    db: AsyncSession,
    user_id: uuid.UUID,
    include_dismissed: bool = False,
    since: datetime | None = None,
    limit: int = 200,
) -> list[ServiceRequestOut]:
    stmt = (
        select(CallSession)
        .options(selectinload(CallSession.property), selectinload(CallSession.notifications))
        .where(CallSession.user_id == user_id)
        .where(CallSession.call_type.in_(SERVICE_REQUEST_CALL_TYPES))
        .order_by(CallSession.created_at.desc())
        .limit(limit)
    )
    if not include_dismissed:
        stmt = stmt.where(CallSession.dismissed_at.is_(None))
    if since is not None:
        stmt = stmt.where(CallSession.created_at >= since)

    calls = (await db.scalars(stmt)).all()
    return [_to_out(call) for call in calls]


async def bulk_dismiss(db: AsyncSession, user_id: uuid.UUID, call_session_ids: list[uuid.UUID]) -> int:
    """Soft-dismiss: sets dismissed_at, never deletes -- the host can still
    refer back via list_service_requests(include_dismissed=True)."""
    stmt = (
        select(CallSession)
        .where(CallSession.user_id == user_id)
        .where(CallSession.id.in_(call_session_ids))
    )
    calls = (await db.scalars(stmt)).all()
    now = datetime.now(timezone.utc)
    for call in calls:
        call.dismissed_at = now
    await db.commit()
    return len(calls)
