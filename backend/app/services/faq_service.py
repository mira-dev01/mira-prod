"""Host-managed FAQ knowledge base. Entries are added/verified from the
dashboard (app/api/v1/faq.py) or auto-generated on Airbnb import
(app/services/airbnb_import.py); search_faq (the voice tool) only reads.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange
from app.config import settings
from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.models.unanswered_question import UnansweredQuestion
from app.services.airbnb_import import AUTO_FAQ_CATEGORIES


async def search_faq_entries(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    property_id: uuid.UUID | None = None,
) -> list[FaqEntry]:
    pattern = f"%{query}%"
    stmt = select(FaqEntry).where(
        FaqEntry.user_id == user_id,
        FaqEntry.status == "verified",
        or_(FaqEntry.question.ilike(pattern), FaqEntry.answer.ilike(pattern), FaqEntry.category.ilike(pattern)),
    )
    if property_id is not None:
        stmt = stmt.where(or_(FaqEntry.property_id == property_id, FaqEntry.property_id.is_(None)))
        # Property-specific answers should win over a portfolio-wide fallback
        # when both match the same query (e.g. a property-specific "limited
        # roadside parking" override vs. a general Goa-wide parking entry) --
        # property_id.is_(None) sorts as True (1) after False (0), so
        # property-specific rows come first.
        stmt = stmt.order_by(FaqEntry.property_id.is_(None))
    stmt = stmt.limit(3)
    return list((await db.scalars(stmt)).all())


async def search_legacy_property_faq(db: AsyncSession, property_id: uuid.UUID, query: str) -> list[dict]:
    """Fallback for the older inline Property.faq JSON field, for properties
    that haven't been migrated to structured FaqEntry rows yet."""
    property_ = await db.get(Property, property_id)
    if property_ is None or not property_.faq:
        return []
    query_lower = query.lower()
    return [
        item
        for item in property_.faq
        if query_lower in item.get("question", "").lower() or query_lower in item.get("answer", "").lower()
    ][:3]


async def list_faq_entries(db: AsyncSession, user_id: uuid.UUID) -> list[FaqEntry]:
    return list(
        (
            await db.scalars(
                select(FaqEntry).where(FaqEntry.user_id == user_id).order_by(FaqEntry.created_at.desc())
            )
        ).all()
    )


async def get_owned_faq_entry(db: AsyncSession, faq_id: uuid.UUID, user_id: uuid.UUID) -> FaqEntry | None:
    entry = await db.get(FaqEntry, faq_id)
    if entry is None or entry.user_id != user_id:
        return None
    return entry


async def sync_imported_faq_entries(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_id: uuid.UUID,
    faq_entries: list[dict],
) -> int:
    """Replaces this property's auto-imported FAQ entries (categories in
    AUTO_FAQ_CATEGORIES) with a fresh batch, so re-importing a refreshed
    scrape updates the knowledge base instead of piling up duplicates. FAQ
    entries the host added by hand (any other category) are left alone."""
    await db.execute(
        delete(FaqEntry).where(
            FaqEntry.user_id == user_id,
            FaqEntry.property_id == property_id,
            FaqEntry.category.in_(AUTO_FAQ_CATEGORIES),
        )
    )
    for entry in faq_entries:
        db.add(
            FaqEntry(
                user_id=user_id,
                property_id=property_id,
                question=entry["question"],
                answer=entry["answer"],
                category=entry.get("category"),
                status="verified",
                verified_by="airbnb_import",
            )
        )
    await db.commit()
    return len(faq_entries)


@dataclass
class FaqGap:
    """One row per distinct question search_faq had no verified answer for,
    grouped by normalized_question with a frequency count -- the FAQ
    Learning Engine's "unanswered questions" list (app/api/v1/faq.py)."""

    sample_id: uuid.UUID  # id of the most-recently-asked row in this group; used to answer the whole group
    normalized_question: str
    question: str  # most recent raw question text, for display
    count: int
    property_id: uuid.UUID | None
    last_asked_at: datetime


async def list_faq_gaps(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_id: uuid.UUID | None = None,
    date_range: DateRange | None = None,
) -> list[FaqGap]:
    """Groups pending UnansweredQuestion rows by normalized_question,
    ranked by frequency. property_id/date_range narrow which occurrences
    count, but every group's displayed question/property/timestamp comes
    from its most recent occurrence."""
    filters = [UnansweredQuestion.user_id == user_id, UnansweredQuestion.status == "pending"]
    if property_id is not None:
        filters.append(UnansweredQuestion.property_id == property_id)
    if date_range is not None:
        if date_range.since is not None:
            filters.append(UnansweredQuestion.created_at >= date_range.since)
        if date_range.until is not None:
            filters.append(UnansweredQuestion.created_at < date_range.until)

    # DISTINCT ON picks one representative row per normalized_question --
    # the most recent, via the matching ORDER BY -- while the subquery below
    # supplies the frequency count for that same group.
    counts_subq = (
        select(
            UnansweredQuestion.normalized_question,
            func.count().label("count"),
        )
        .where(*filters)
        .group_by(UnansweredQuestion.normalized_question)
        .subquery()
    )
    representative = (
        select(UnansweredQuestion)
        .distinct(UnansweredQuestion.normalized_question)
        .where(*filters)
        .order_by(UnansweredQuestion.normalized_question, UnansweredQuestion.created_at.desc())
        .subquery()
    )
    stmt = (
        select(
            representative.c.id,
            representative.c.normalized_question,
            representative.c.question,
            representative.c.property_id,
            representative.c.created_at,
            counts_subq.c.count,
        )
        .join(counts_subq, counts_subq.c.normalized_question == representative.c.normalized_question)
        .order_by(counts_subq.c.count.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        FaqGap(
            sample_id=row.id,
            normalized_question=row.normalized_question,
            question=row.question,
            count=row.count,
            property_id=row.property_id,
            last_asked_at=row.created_at,
        )
        for row in rows
    ]


async def faq_gap_analytics(db: AsyncSession, user_id: uuid.UUID, bucket: str = "week") -> dict:
    """Three breakdowns for the FAQ Learning Engine's analytics view:
    most-frequent questions, by-property counts, and a time trend bucketed
    by week or month."""
    base_filters = [UnansweredQuestion.user_id == user_id, UnansweredQuestion.status == "pending"]

    most_frequent_stmt = (
        select(
            UnansweredQuestion.normalized_question,
            func.max(UnansweredQuestion.question).label("question"),
            func.count().label("count"),
        )
        .where(*base_filters)
        .group_by(UnansweredQuestion.normalized_question)
        .order_by(func.count().desc())
        .limit(20)
    )
    most_frequent = [
        {"question": row.question, "count": row.count} for row in (await db.execute(most_frequent_stmt)).all()
    ]

    by_property_stmt = (
        select(UnansweredQuestion.property_id, func.count().label("count"))
        .where(*base_filters)
        .group_by(UnansweredQuestion.property_id)
        .order_by(func.count().desc())
    )
    by_property = [
        {"property_id": row.property_id, "count": row.count} for row in (await db.execute(by_property_stmt)).all()
    ]

    day = func.date_trunc(bucket, func.timezone("UTC", UnansweredQuestion.created_at))
    over_time_stmt = (
        select(day.label("bucket"), func.count().label("count")).where(*base_filters).group_by(day).order_by(day)
    )
    over_time = [
        {"bucket": row.bucket.date().isoformat(), "count": row.count}
        for row in (await db.execute(over_time_stmt)).all()
    ]

    return {"most_frequent": most_frequent, "by_property": by_property, "over_time": over_time}


async def transcribe_gap_answer_audio(audio) -> str:
    """Transcribes a host's recorded voice answer (dashboard FAQ Learning
    Engine "answer via voice" flow) to text via Sarvam's plain batch/REST
    speech-to-text API.

    Deliberately NOT pipecat's SarvamSTTService (app/voice/pipeline.py) --
    that class only works inside a live streaming pipeline (audio in over an
    open WebSocket, transcripts out via callbacks as the call progresses).
    There's no live call here, just one pre-recorded clip, so the sarvamai
    SDK's own AsyncSpeechToTextClient.transcribe() (a synchronous
    request/response REST call) is the right tool -- same model + mode as
    the live voice pipeline for consistent Hindi/Hinglish handling.
    """
    from sarvamai import AsyncSarvamAI

    client = AsyncSarvamAI(api_subscription_key=settings.sarvam_api_key)
    audio_bytes = await audio.read()
    response = await client.speech_to_text.transcribe(
        file=(audio.filename, audio_bytes, audio.content_type),
        model=settings.sarvam_stt_model,
        mode="codemix",
    )
    return response.transcript.strip()


async def get_owned_unanswered_question(
    db: AsyncSession, gap_id: uuid.UUID, user_id: uuid.UUID
) -> UnansweredQuestion | None:
    row = await db.get(UnansweredQuestion, gap_id)
    if row is None or row.user_id != user_id:
        return None
    return row


async def answer_faq_gap(
    db: AsyncSession,
    gap: UnansweredQuestion,
    answer: str,
    apply_to_property: bool,
    verified_by: str,
) -> FaqEntry:
    """Converts an unanswered-question group into a real, verified FaqEntry,
    and marks every pending row sharing the same normalized_question (i.e.
    the whole group, not just the one `gap` row used to look it up) as
    answered -- so the group actually disappears from list_faq_gaps."""
    entry = FaqEntry(
        user_id=gap.user_id,
        property_id=gap.property_id if apply_to_property else None,
        question=gap.question,
        answer=answer,
        category="host_answered",
        status="verified",
        verified_by=verified_by,
    )
    db.add(entry)
    await db.flush()  # need entry.id before the bulk-update below

    await db.execute(
        UnansweredQuestion.__table__.update()
        .where(
            UnansweredQuestion.user_id == gap.user_id,
            UnansweredQuestion.normalized_question == gap.normalized_question,
            UnansweredQuestion.status == "pending",
        )
        .values(status="answered", resolved_faq_entry_id=entry.id)
    )
    await db.commit()
    await db.refresh(entry)
    return entry
