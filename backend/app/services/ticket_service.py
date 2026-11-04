"""Tickets: the durable follow-up behind everything the agent tells a guest the
host (or someone) will do.

A guest hearing "I've flagged this to the host" is a promise. The notification
that goes with it scrolls away; the ticket stays open until the host marks it
done. Handlers open one at the moment they make the promise
(tool_handlers.py), and an end-of-call check (reconcile_promises) catches a
promise the model made in words that no tool recorded.

Opening a ticket runs on the live-call path, so it never raises: a failure is
logged and the call carries on, same discipline as every other side effect
here.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.property import Property
from app.models.ticket import Ticket
from app.services import notification_service

logger = logging.getLogger(__name__)

_URGENCIES = ("low", "medium", "high", "emergency")
_PROMISE_MARKER = "Also promised on the call:"


async def open_ticket(
    db: AsyncSession,
    *,
    kind: str,
    summary: str,
    detail: str | None = None,
    urgency: str = "medium",
    user_id: uuid.UUID | None = None,
    property_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    notification_id: uuid.UUID | None = None,
    source: str = "tool",
) -> Ticket | None:
    """Opens (or returns the existing) ticket. The same promise made twice in
    one call -- the model calling a tool again -- is one ticket, not two.
    Returns None, never raises, if it cannot be saved."""
    saved = False
    try:
        summary = (summary or "").strip()[:255]
        if not summary:
            return None
        if user_id is None:
            if property_id is not None:
                user_id = await db.scalar(select(Property.user_id).where(Property.id == property_id))
            if user_id is None and call_session_id is not None:
                user_id = await db.scalar(select(CallSession.user_id).where(CallSession.id == call_session_id))
        if user_id is None:
            logger.warning("open_ticket: no host to attach a %r ticket to; skipped", kind)
            return None

        if call_session_id is not None:
            existing = await db.scalar(
                select(Ticket).where(
                    Ticket.call_session_id == call_session_id, Ticket.kind == kind, Ticket.summary == summary
                )
            )
            if existing is not None:
                return existing

        ticket = Ticket(
            user_id=user_id,
            property_id=property_id,
            call_session_id=call_session_id,
            notification_id=notification_id,
            kind=kind,
            summary=summary,
            detail=detail,
            urgency=urgency if urgency in _URGENCIES else "medium",
            source=source,
        )
        # The insert runs in a savepoint so that if it fails only the ticket is
        # undone. A plain rollback would expire every object the calling handler
        # still holds (the lead, the property) and break the rest of its work.
        async with db.begin_nested():
            db.add(ticket)
        saved = True
        await db.commit()
        await db.refresh(ticket)
        return ticket
    except Exception:
        if saved:  # the failure was the final commit itself, which leaves nothing usable to keep
            await db.rollback()
        logger.exception("open_ticket failed for kind=%s call_session_id=%s", kind, call_session_id)
        return None


async def list_tickets(
    db: AsyncSession,
    user_id: uuid.UUID,
    status: str = "open",
    property_id: uuid.UUID | None = None,
    limit: int = 200,
    ticket_id: uuid.UUID | None = None,
) -> list[dict]:
    """The host's tickets, newest first, with the property and guest named.
    status: open | resolved (done or dismissed) | all."""
    stmt = (
        select(Ticket, Property.name, CallSession.caller_number, Lead.guest_name, Lead.phone)
        .outerjoin(Property, Property.id == Ticket.property_id)
        .outerjoin(CallSession, CallSession.id == Ticket.call_session_id)
        .outerjoin(Lead, Lead.id == CallSession.lead_id)
        .where(Ticket.user_id == user_id)
        .order_by(Ticket.created_at.desc())
        .limit(limit)
    )
    if status == "open":
        stmt = stmt.where(Ticket.status == "open")
    elif status == "resolved":
        stmt = stmt.where(Ticket.status != "open")
    if property_id is not None:
        stmt = stmt.where(Ticket.property_id == property_id)
    if ticket_id is not None:
        stmt = stmt.where(Ticket.id == ticket_id)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": ticket.id,
            "kind": ticket.kind,
            "summary": ticket.summary,
            "detail": ticket.detail,
            "urgency": ticket.urgency,
            "status": ticket.status,
            "source": ticket.source,
            "created_at": ticket.created_at,
            "resolved_at": ticket.resolved_at,
            "property_id": ticket.property_id,
            "property_name": property_name,
            "call_session_id": ticket.call_session_id,
            "guest_name": guest_name,
            "guest_phone": lead_phone or caller_number,
        }
        for ticket, property_name, caller_number, guest_name, lead_phone in rows
    ]


async def get_owned_ticket(db: AsyncSession, ticket_id: uuid.UUID, user_id: uuid.UUID) -> Ticket | None:
    ticket = await db.get(Ticket, ticket_id)
    return ticket if ticket is not None and ticket.user_id == user_id else None


async def set_status(db: AsyncSession, ticket: Ticket, status: str) -> Ticket:
    ticket.status = status
    ticket.resolved_at = None if status == "open" else datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def reconcile_promises(
    db: AsyncSession, *, host_user_id: uuid.UUID, call_session_id: uuid.UUID | None, promises: list[str]
) -> None:
    """End-of-call backstop. `promises` are what Mira told the guest would
    happen after the call, as read from the transcript. A call with no ticket
    at all gets one for them (and the host an alert); a call that already has
    tickets gets the promises added to the newest, so the host sees every one
    in a place that stays open. Safe to run twice."""
    promises = [p.strip() for p in promises if p and p.strip()]
    if call_session_id is None or host_user_id is None or not promises:
        return
    try:
        tickets = list(
            (
                await db.scalars(
                    select(Ticket).where(Ticket.call_session_id == call_session_id).order_by(Ticket.created_at.desc())
                )
            ).all()
        )
        listing = "\n".join(f"- {p}" for p in promises)
        if tickets:
            newest = tickets[0]
            if _PROMISE_MARKER not in (newest.detail or ""):
                newest.detail = f"{newest.detail or ''}\n\n{_PROMISE_MARKER}\n{listing}".strip()
                await db.commit()
            return

        extra = f" (+{len(promises) - 1} more)" if len(promises) > 1 else ""
        notification = await notification_service.create_notification(
            db,
            channel="system",
            call_session_id=call_session_id,
            urgency="medium",
            message=f"Mira told a guest: {promises[0]}{extra}. Nothing was recorded for the host during the call -- "
            "see Leads > Tickets.",
        )
        await open_ticket(
            db,
            kind="promise",
            summary=f"{promises[0]}{extra}",
            detail=f"Mira told the guest:\n{listing}",
            user_id=host_user_id,
            call_session_id=call_session_id,
            notification_id=notification.id,
            source="post_call",
        )
    except Exception:
        await db.rollback()
        logger.exception("reconcile_promises failed for call_session_id=%s", call_session_id)
