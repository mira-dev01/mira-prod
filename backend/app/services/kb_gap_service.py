"""Records "Mira couldn't answer this" gaps for the host's FAQ dashboard.

Three paths feed it (UnansweredQuestion.source):
  search_miss       search_faq's knowledge-base search found nothing on file.
  guard_blocked     the grounding guard replaced a spoken claim that had no
                    support on file (app/voice/kb_grounding_guard.py).
  miss_line_spoken  Mira said the not-on-file line after a "possible" result.

All three land in the same UnansweredQuestion table the existing FAQ Learning
Engine reads (app/api/v1/faq.py /faq/gaps), always with the property it was
about, so the host sees exactly which property Mira couldn't answer for and
can answer it once -- which creates a verified FaqEntry and re-indexes that
property's facts (see answer_faq_gap).

Not everything Mira can't answer is a missing FAQ, so log_kb_gap sorts what it
is given first (classify_gap):
  skip       filler, a one-word reply or a "say that again" -- nothing to answer
             or follow up, so nothing is recorded.
  request    an availability, price or payment-link ask. The agent has its own
             tools for these, so "not on file" is the wrong box: the host gets a
             ticket (Tickets raised) plus an escalation email instead, so the
             guest is followed up rather than left with a miss line.
  knowledge  a genuine property question -- the FAQ gap path below.

Nothing here may break a live call: callers wrap it in try/except, and it
never raises on a duplicate.
"""

import html as html_lib
import logging
import re
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.call_session import CallSession
from app.models.notification import Notification
from app.models.property import Property
from app.models.ticket import Ticket
from app.models.unanswered_question import UnansweredQuestion
from app.models.user import User
from app.services import embedding_service, notification_preferences_service, notification_service, ticket_service
from app.utils import background
from app.utils.throttle import Throttle

logger = logging.getLogger(__name__)

_NOTIFICATION_PREFIX = "Mira couldn't answer"
_MAX_QUESTIONS_IN_NOTIFICATION = 5

# One request email per call per window: a guest asking three things in one call
# is one email (every request still gets its own ticket).
_request_email_throttle = Throttle(timedelta(minutes=30))

_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"

# Nothing to answer: acknowledgements, "what?", asking Mira to repeat herself, or a
# guest complaining they already said it. Matched on the whole text.
_FILLER_RE = re.compile(
    r"^\W*(?:"
    r"(?:what|huh|pardon|sorry|hello|hi|hey|yes|yeah|no|ok(?:ay)?|hmm+|thanks?(?: you)?|bye)"
    r"|.*\b(?:didn'?t|did not|couldn'?t|could not|can'?t) (?:quite )?(?:catch|hear|understand)\b.*"
    r"|.*\b(?:say|repeat) (?:that|it) (?:once )?(?:again|please)\b.*"
    r"|.*\b(?:could|can|please|would) you repeat\b.*"
    r"|.*\bi (?:already |just )?(?:told|said to) you\b.*"
    r")\W*$",
    re.IGNORECASE,
)

# Things the agent has a tool or a host action for, not a fact to file in the FAQ.
_PAYMENT_RE = re.compile(r"\bpay(?:ment|ments)?\b|\bpayment link\b|\bupi\b|\binvoice\b", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"\bprice[sd]?\b|\bpricing\b|\brates?\b|\bcost\b|\bhow much\b|\btariff\b|\bquote\b|\bdiscount\b|\bcharges?\b",
    re.IGNORECASE,
)
_AVAILABILITY_RE = re.compile(
    r"\bavailab(?:le|ility)\b|\bvacan(?:t|cy)\b|\bcheck\b.*\b(?:for|on)\b.*(?:\b\d{1,2}(?:st|nd|rd|th)?\b|\b(?:" + _MONTHS + r")\b)",
    re.IGNORECASE,
)


def classify_gap(question: str) -> str:
    """"skip", "request" or "knowledge" -- see the module docstring."""
    text = (question or "").strip()
    if len(re.findall(r"\w+", text)) <= 1 or _FILLER_RE.match(text):
        return "skip"
    if _PAYMENT_RE.search(text) or _PRICE_RE.search(text) or _AVAILABILITY_RE.search(text):
        return "request"
    return "knowledge"


async def log_kb_gap(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
    question: str,
    source: str = "search_miss",
) -> UnansweredQuestion | None:
    """Writes one gap row (deduped per call on the normalized question) and
    adds it to the call's single host notification. Returns the new row, or
    None when this call already logged the same question."""
    question = (question or "").strip()
    if not question:
        return None
    verdict = classify_gap(question)
    if verdict == "skip":
        return None
    if verdict == "request":
        await _raise_guest_request(db, user_id, property_id, call_session_id, question)
        return None
    normalized = question.lower()[:500]

    if call_session_id is not None:
        already = await db.scalar(
            select(UnansweredQuestion.id).where(
                UnansweredQuestion.call_session_id == call_session_id,
                UnansweredQuestion.normalized_question == normalized,
            )
        )
        if already is not None:
            return None

    gap = UnansweredQuestion(
        user_id=user_id,
        property_id=property_id,
        call_session_id=call_session_id,
        question=question,
        normalized_question=normalized,
        source=source,
    )
    db.add(gap)
    await db.commit()
    await db.refresh(gap)
    # Knowledge Memory: embedding computed fire-and-forget AFTER the commit
    # (own session), so it never adds latency to the live guest turn.
    background.spawn(embedding_service.backfill_unanswered_question_embedding(gap.id, gap.question))

    await _notify_host(db, user_id, property_id, call_session_id, question)
    return gap


async def _notify_host(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
    question: str,
) -> None:
    """One low-urgency in-app notification per call: the first unanswered
    question creates it, later ones in the same call are appended to it, so a
    guest asking five things Mira lacks doesn't produce five alerts."""
    existing: Notification | None = None
    if call_session_id is not None:
        existing = await db.scalar(
            select(Notification).where(
                Notification.call_session_id == call_session_id,
                Notification.channel == "system",
                Notification.message.like(f"{_NOTIFICATION_PREFIX}%"),
            )
        )

    if existing is not None:
        if existing.message.count('"') // 2 < _MAX_QUESTIONS_IN_NOTIFICATION:
            existing.message = f'{existing.message}; "{question}"'
            await db.commit()
        return

    property_name = ""
    if property_id is not None:
        property_ = await db.get(Property, property_id)
        if property_ is not None:
            property_name = f" about {property_.display_name or property_.name}"
    await notification_service.create_notification(
        db,
        channel="system",
        message=(
            f'{_NOTIFICATION_PREFIX} a guest question{property_name}: "{question}". '
            "Answer it under 'Questions Mira couldn\'t answer' on the FAQ knowledge base page so Mira can answer it next time."
        ),
        property_id=property_id,
        call_session_id=call_session_id,
        urgency="low",
    )


async def _raise_guest_request(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
    question: str,
) -> None:
    """An availability / price / payment-link ask Mira could not settle on the
    call. The guest was left waiting on the host, so the host gets a ticket
    (Tickets raised), an in-app alert, and -- unless they turned escalation
    emails off -- an email, the same one escalations use. One ticket per
    distinct ask per call; one email per call per window."""
    summary = f"Follow up with the guest: {question}"[:255]
    if call_session_id is not None:
        already = await db.scalar(
            select(Ticket.id).where(
                Ticket.call_session_id == call_session_id, Ticket.kind == "request", Ticket.summary == summary
            )
        )
        if already is not None:
            return

    property_ = await db.get(Property, property_id) if property_id is not None else None
    property_name = (property_.display_name or property_.name) if property_ is not None else None
    about = f" about {property_name}" if property_name else ""
    message = f'A guest asked{about}: "{question}". Mira could not answer it on the call, so please follow up with them.'
    notification = await notification_service.create_notification(
        db,
        channel="system",
        property_id=property_id,
        call_session_id=call_session_id,
        urgency="medium",
        message=message,
    )
    ticket = await ticket_service.open_ticket(
        db,
        kind="request",
        summary=summary,
        detail=message,
        urgency="medium",
        user_id=user_id,
        property_id=property_id,
        call_session_id=call_session_id,
        notification_id=notification.id,
    )
    if ticket is None:
        return

    host = await db.get(User, user_id)
    if host is None or not notification_preferences_service.get(host).escalation_email:
        return
    send, held_back = _request_email_throttle.take(str(call_session_id or user_id))
    if not send:
        return
    from app.services.tool_handlers import _send_escalation_email  # local: tool_handlers imports this module

    guest = await db.scalar(select(CallSession.caller_number).where(CallSession.id == call_session_id)) if call_session_id else None
    link = f"{settings.frontend_base_url}/dashboard/leads?tab=tickets"
    more = f"\n\n({held_back} other request(s) were raised since the last email.)" if held_back else ""
    body = (
        f"{message}\n\nGuest: {guest or 'number not captured'}\nProperty: {property_name or 'not known'}{more}"
        f"\n\nView in dashboard: {link}"
    )
    # The question is the guest's own words: escape it before it goes into HTML.
    html = "<p>" + html_lib.escape(body).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    background.spawn(
        _send_escalation_email(
            host.notification_email or host.email,
            f"[Action needed] A guest is waiting for a follow-up{about}",
            body,
            html,
        )
    )
