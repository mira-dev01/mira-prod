"""Per-call summary email, fired once for every call that reaches
on_pipeline_finished normally (see app/voice/pipeline.py) -- independent of
whether the call was escalated. Reuses the same email_client/email_templates
infrastructure as tool_handlers.py's escalation/photos emails rather than
inventing a second transport.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.integrations import email_client
from app.integrations.email_templates import build_call_summary_email_html
from app.models.call_session import CallSession
from app.models.user import User
from app.schemas.call_summary import CallSummary

logger = logging.getLogger(__name__)


async def send_call_summary_email(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Fire-and-forget (caller wraps this in asyncio.create_task) -- never
    raises, so a slow/misconfigured SMTP server can't affect pipeline
    teardown. Silently no-ops for a call with no resolvable host, matching
    every other best-effort send in this codebase.
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

        # session.lead may be None -- a call only gets a Lead row if
        # update_lead/escalate_to_host/the pricing-engagement safety net
        # fired during it, and a junk/incomplete call has its Lead row
        # actively deleted at this same teardown point (see
        # lead_service.delete_if_empty/delete_for_unqualified_call, called
        # just before this function in on_pipeline_finished). Only "hot"
        # renders a badge -- "warm"/"cold"/None all render identically (no
        # badge) so a routine call doesn't get visual noise.
        lead_temperature = session.lead.lead_temperature if session.lead is not None else None
        property_name = session.property.name if session.property is not None else "your account"
        call_page_url = f"{settings.frontend_base_url}/dashboard/calls/{call_session_id}"

        summary = CallSummary.model_validate(session.ai_summary) if session.ai_summary else None
        conversation_summary = summary.conversation_summary if summary else "No summary available."

        hot_prefix = "\U0001F525 Hot lead — " if lead_temperature == "hot" else ""
        subject = f"{hot_prefix}Call summary: {property_name}"

        body = (
            f"Call summary for {property_name}\n\n"
            f"{conversation_summary}\n\n"
            f"Guest: {session.guest_phone or 'Not captured'}\n"
            f"Call type: {session.call_type}\n\n"
            f"View full call: {call_page_url}"
        )
        html_body = build_call_summary_email_html(
            property_name=property_name,
            guest_name=session.guest_name,
            guest_phone=session.guest_phone,
            call_type=session.call_type,
            conversation_summary=conversation_summary,
            duration_minutes=session.duration_minutes,
            lead_temperature=lead_temperature,
            call_page_url=call_page_url,
        )

        to_email = host_user.notification_email or host_user.email
        result = await email_client.send_email(to_email, subject, body, html_body=html_body)
        if result.get("status") == "skipped":
            logger.info("Call summary email to %s skipped: %s", to_email, result.get("reason"))
    except Exception:
        logger.exception("Failed to send call summary email for call_session_id=%s", call_session_id)
