"""Per-call summary email, fired once for every call that reaches
on_pipeline_finished normally (see app/voice/pipeline.py) -- independent of
whether the call was escalated. Reuses the same email_client/email_templates
infrastructure as tool_handlers.py's escalation/photos emails rather than
inventing a second transport.

The host controls it from Settings > Notifications: on/off, their own lead
labels, and an optional subject/body template. render_call_summary_email is
the one renderer, used by both the real send and the dashboard preview, so
the preview can never drift from what is sent.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.integrations import email_client
from app.integrations.email_templates import build_call_summary_email_html
from app.models.call_session import CallSession
from app.models.user import User
from app.schemas.call_summary import CallSummary
from app.schemas.notification_preferences import (
    DEFAULT_CALL_SUMMARY_SUBJECT,
    NotificationPreferences,
    render_template,
)
from app.services import notification_preferences_service
from app.utils.dates import IST

logger = logging.getLogger(__name__)


@dataclass
class CallSummaryFacts:
    property_name: str
    guest_name: str | None
    guest_phone: str | None
    call_type: str
    conversation_summary: str
    duration_minutes: float | None
    started_at: datetime | None
    lead_temperature: str | None
    call_page_url: str


def render_call_summary_email(
    prefs: NotificationPreferences,
    facts: CallSummaryFacts,
    *,
    subject_template: str | None = None,
    body_template: str | None = None,
) -> tuple[str, str, str]:
    """Returns (subject, plain-text body, html body). The templates default
    to the host's saved ones; the preview passes unsaved drafts instead."""
    subject_template = subject_template or prefs.call_summary_subject or DEFAULT_CALL_SUMMARY_SUBJECT
    body_template = body_template if body_template is not None else prefs.call_summary_body
    lead_label = notification_preferences_service.lead_label(prefs, facts.lead_temperature)
    values = {
        "guest_name": facts.guest_name,
        "guest_phone": facts.guest_phone,
        "property_name": facts.property_name,
        "lead_label": lead_label,
        "call_summary": facts.conversation_summary,
        "call_time": facts.started_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p") if facts.started_at else None,
        "call_duration": f"{facts.duration_minutes:g} min" if facts.duration_minutes is not None else None,
        "dashboard_link": facts.call_page_url,
    }
    subject = " ".join(render_template(subject_template, values).split())
    custom_body = render_template(body_template, values) if body_template else None
    text_body = custom_body or (
        f"Call summary for {facts.property_name}\n\n"
        f"{facts.conversation_summary}\n\n"
        f"Guest: {facts.guest_phone or 'Not captured'}\n"
        f"Call type: {facts.call_type}\n\n"
        f"View full call: {facts.call_page_url}"
    )
    html_body = build_call_summary_email_html(
        property_name=facts.property_name,
        guest_name=facts.guest_name,
        guest_phone=facts.guest_phone,
        call_type=facts.call_type,
        conversation_summary=facts.conversation_summary,
        duration_minutes=facts.duration_minutes,
        lead_temperature=facts.lead_temperature,
        call_page_url=facts.call_page_url,
        lead_label=lead_label,
        custom_body=custom_body,
    )
    return subject, text_body, html_body


async def send_call_summary_email(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Fire-and-forget (caller wraps this in asyncio.create_task) -- never
    raises, so a slow/misconfigured SMTP server can't affect pipeline
    teardown. Silently no-ops for a call with no resolvable host, or when
    the host turned call-summary emails off, matching every other
    best-effort send in this codebase.
    """
    if call_session_id is None:
        return
    try:
        # Eager-load lead/property/guest_profile -- CallSession.guest_name/
        # guest_phone (computed properties) and this function both touch
        # those relationships, and SQLAlchemy's default lazy ("select")
        # loading raises MissingGreenlet under the async driver if accessed
        # without an active await context, which a plain db.get() doesn't
        # give them.
        result = await db.execute(
            select(CallSession)
            .where(CallSession.id == call_session_id)
            .options(
                selectinload(CallSession.lead),
                selectinload(CallSession.property),
                selectinload(CallSession.guest_profile),
            )
        )
        session = result.scalar_one_or_none()
        if session is None or session.user_id is None:
            return
        host_user = await db.get(User, session.user_id)
        if host_user is None:
            return
        prefs = notification_preferences_service.get(host_user)
        if not prefs.call_summary_email:
            return
        # session.lead may be None -- a call only gets a Lead row if
        # update_lead/escalate_to_host/the pricing-engagement safety net
        # fired during it, and a junk/incomplete call has its Lead row
        # actively deleted at this same teardown point (see
        # lead_service.delete_if_empty/delete_for_unqualified_call, called
        # just before this function in on_pipeline_finished). No lead = the
        # host's "not qualified" label.
        summary = CallSummary.model_validate(session.ai_summary) if session.ai_summary else None
        facts = CallSummaryFacts(
            property_name=session.property.name if session.property is not None else "your account",
            guest_name=session.guest_name,
            guest_phone=session.guest_phone,
            call_type=session.call_type,
            conversation_summary=summary.conversation_summary if summary else "No summary available.",
            duration_minutes=session.duration_minutes,
            started_at=session.started_at,
            lead_temperature=session.lead.lead_temperature if session.lead is not None else None,
            call_page_url=f"{settings.frontend_base_url}/dashboard/calls/{call_session_id}",
        )
        subject, body, html_body = render_call_summary_email(prefs, facts)
        to_email = host_user.notification_email or host_user.email
        result = await email_client.send_email(to_email, subject, body, html_body=html_body)
        if result.get("status") == "skipped":
            logger.info("Call summary email to %s skipped: %s", to_email, result.get("reason"))
    except Exception:
        logger.exception("Failed to send call summary email for call_session_id=%s", call_session_id)
