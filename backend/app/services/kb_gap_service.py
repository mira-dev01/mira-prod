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

Nothing here may break a live call: callers wrap it in try/except, and it
never raises on a duplicate.
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.property import Property
from app.models.unanswered_question import UnansweredQuestion
from app.services import embedding_service, notification_service

_NOTIFICATION_PREFIX = "Mira couldn't answer"
_MAX_QUESTIONS_IN_NOTIFICATION = 5


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
    asyncio.create_task(embedding_service.backfill_unanswered_question_embedding(gap.id, gap.question))

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
