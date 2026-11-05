"""Immediate alerts when a call that should have reached Mira didn't.

The host and the admins hear about it right away, so a person can ring the
guest back while Mira is being fixed. Four things count (the reason keys
below): the voice pipeline crashed mid-call, a call was left stuck and had to
be swept up, Exotel reported the call failed / went unanswered without ever
reaching Mira, and a guest who spoke was never answered.

Per failed call the host always gets a ticket and an in-app alert (nothing is
throttled, so none is lost). The louder channels -- the host's WhatsApp and
email, and the admins' email -- are throttled so an outage of fifty calls is
one message and a rollup, not fifty: the first goes out immediately, later ones
within ALERT_WINDOW are counted and folded into the next. The throttle lives
in this process; with several workers an outage can send one message per
worker, which is acceptable for an alert.

Never raises: it runs on teardown and webhook paths.
"""

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.call_session import CallSession
from app.models.property import Property
from app.models.ticket import Ticket
from app.models.user import User
from app.services import notification_preferences_service, notification_service, ticket_service
from app.utils.throttle import Throttle

logger = logging.getLogger(__name__)

ALERT_WINDOW = timedelta(minutes=5)

REASONS = {
    "pipeline_crash": "Mira's voice system hit an error during the call.",
    "never_finished": "The call never finished properly on Mira's side.",
    "not_connected": "The call did not connect to Mira.",
    "never_answered": "The guest spoke but Mira never replied.",
}


_host_throttle = Throttle(ALERT_WINDOW)
_admin_throttle = Throttle(ALERT_WINDOW)

# Detached alert tasks are kept here so they are not garbage-collected mid-send.
_background: set[asyncio.Task] = set()


def spawn_alert(call_session_id: uuid.UUID | None, reason_key: str, detail: str | None = None) -> None:
    """Fire-and-forget, for callers that must not wait on network sends."""
    if call_session_id is None:
        return
    task = asyncio.create_task(alert_failed_call(call_session_id, reason_key, detail))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def alert_failed_call(call_session_id: uuid.UUID, reason_key: str, detail: str | None = None) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await _alert(db, call_session_id, reason_key, detail)
    except Exception:
        logger.exception("missed-call alert failed for call_session_id=%s", call_session_id)


async def _alert(db, call_session_id: uuid.UUID, reason_key: str, detail: str | None) -> None:
    from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

    session = await db.get(CallSession, call_session_id)
    if session is None or session.caller_number == BROWSER_TEST_CALLER_NUMBER:
        return  # a test call in the browser is nobody's missed guest
    already = await db.scalar(
        select(Ticket.id).where(Ticket.call_session_id == call_session_id, Ticket.kind == "missed_call")
    )
    if already is not None:
        return  # this call has been raised (e.g. crash handler, then the sweep)

    reason = REASONS.get(reason_key, REASONS["not_connected"]) + (f" ({detail})" if detail else "")
    caller = session.caller_number
    host = await db.get(User, session.user_id) if session.user_id else None
    property_ = await db.get(Property, session.property_id) if session.property_id else None
    property_name = (property_.display_name or property_.name) if property_ is not None else None
    tickets_url = f"{settings.frontend_base_url}/dashboard/leads?tab=tickets"

    if host is not None:
        message = f"A guest's call didn't connect ({caller or 'number not captured'}). {reason} Please call them back."
        notification = await notification_service.create_notification(
            db,
            channel="system",
            property_id=session.property_id,
            call_session_id=call_session_id,
            urgency="high",
            message=message,
        )
        await ticket_service.open_ticket(
            db,
            kind="missed_call",
            summary=f"Call back {caller or 'the guest'} -- their call didn't connect",
            detail=reason,
            urgency="high",
            user_id=host.id,
            property_id=session.property_id,
            call_session_id=call_session_id,
            notification_id=notification.id,
        )
        send, held_back = _host_throttle.take(str(host.id))
        if send:
            await _message_host(host, property_, property_name, caller, reason, held_back, tickets_url)

    send, held_back = _admin_throttle.take("admin")
    if send:
        await _message_admins(host, caller, reason, held_back)


def _rollup(held_back: int) -> str:
    return f" ({held_back} other call(s) also failed since the last alert.)" if held_back else ""


async def _message_host(host, property_, property_name, caller, reason, held_back, tickets_url) -> None:
    # The same senders escalations use: one WhatsApp template/format and one
    # email path to maintain, not a parallel pair.
    from app.services.tool_handlers import _send_escalation_email, _send_escalation_whatsapp

    summary = "Please call them back." + _rollup(held_back)
    phone = notification_preferences_service.host_transfer_phone(host, property_)
    if phone and settings.twilio_enabled:
        await _send_escalation_whatsapp(
            phone,
            urgency="high",
            property_name=property_name or "your account",
            reason=f"Call did not connect. {reason}",
            call_summary=summary,
            guest_phone=caller,
            dashboard_url=tickets_url,
        )
    if notification_preferences_service.get(host).escalation_email:
        body = (
            f"A guest's call didn't connect.\n\nGuest: {caller or 'number not captured'}\n"
            f"Property: {property_name or 'not known'}\n{reason}\n\n{summary}\n\nTickets: {tickets_url}"
        )
        await _send_escalation_email(
            host.notification_email or host.email,
            "[Action needed] A guest's call didn't connect",
            body,
            "<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>",
        )


async def _message_admins(host, caller, reason, held_back) -> None:
    if not settings.health_alerts_active:
        return  # dev and production watch the same accounts; only one should email
    from app.services.tool_handlers import _send_escalation_email

    who = (host.business_name or host.name or host.email) if host is not None else "an unknown host"
    body = (
        f"A call failed to reach Mira.\n\nHost: {who}{f' ({host.email})' if host is not None else ''}\n"
        f"Guest: {caller or 'number not captured'}\n{reason}{_rollup(held_back)}\n\n"
        f"The host has a ticket to call the guest back. Admin: {settings.frontend_base_url}/admin"
    )
    html = "<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    for recipient in settings.alert_email_list:
        await _send_escalation_email(recipient, f"[Mira] A call failed to connect -- {who}", body, html)
