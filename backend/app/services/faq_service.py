"""Host-managed FAQ knowledge base. Entries are added/verified from the
dashboard (app/api/v1/faq.py) or auto-generated on Airbnb import
(app/services/airbnb_import.py); search_faq (the voice tool) only reads.
"""

import asyncio
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
from app.services import embedding_service
from app.services.airbnb_import import AUTO_FAQ_CATEGORIES


async def search_faq_entries(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    property_id: uuid.UUID | None = None,
) -> list[FaqEntry]:
    pattern = f"%{query}%"
    # Trigram similarity (pg_trgm, enabled via migration e7c2a4f8d9b1) catches
    # paraphrases plain substring matching misses. Confirmed live: a guest
    # re-asked essentially the same question, but the LLM phrases the
    # search_faq `query` itself fresh on every call rather than passing the
    # guest's verbatim words -- "distance from Mall Road" the first time,
    # "distance from Manali shale to Mall Road" the second -- so the
    # exact-substring ILIKE check missed it entirely even though the host
    # had already verified an answer, and the guest was told "I don't have
    # that" for something already on file. 0.35 threshold picked
    # empirically: real paraphrases of the same question scored 0.47-0.63 in
    # testing, unrelated questions scored 0.09-0.24 -- clean separation with
    # margin on both sides.
    question_similarity = func.similarity(FaqEntry.question, query)
    stmt = select(FaqEntry).where(
        FaqEntry.user_id == user_id,
        FaqEntry.status == "verified",
        or_(
            FaqEntry.question.ilike(pattern),
            FaqEntry.answer.ilike(pattern),
            FaqEntry.category.ilike(pattern),
            question_similarity > 0.35,
        ),
    )
    if property_id is not None:
        stmt = stmt.where(or_(FaqEntry.property_id == property_id, FaqEntry.property_id.is_(None)))
        # Property-specific answers should win over a portfolio-wide fallback
        # when both match the same query (e.g. a property-specific "limited
        # roadside parking" override vs. a general Goa-wide parking entry) --
        # property_id.is_(None) sorts as True (1) after False (0), so
        # property-specific rows come first. Best match within that tier
        # next, so the closest question (not just DB row order) wins the
        # limit(3) cut.
        stmt = stmt.order_by(FaqEntry.property_id.is_(None), question_similarity.desc())
    else:
        stmt = stmt.order_by(question_similarity.desc())
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


def full_property_context(property_: Property) -> str:
    """Final search_faq fallback -- every fact on file for this property, in
    one block, handed to the model to pick the actual answer out of.

    Deliberately NOT keyword-matched against the query (an earlier version of
    this had separate search_neighborhood_info/search_amenities helpers doing
    sentence/keyword overlap, tuned per field). That approach quietly missed
    real, on-file answers twice in testing -- once for a neighborhood
    question, once for an amenity -- because keyword overlap is inherently
    guessable-around: any phrasing that doesn't share a token with the source
    text falls through to an unnecessary escalation. The host's own
    requirement is stronger than "usually finds it": anything on file must
    resolve, every time, and escalation is reserved for what's genuinely not
    there. Handing over everything and letting the model itself read for the
    answer is what actually guarantees that.

    Covers exactly what Guest Support's system prompt gets for free (see
    build_system_prompt) -- this is what gives a Lead Agent (portfolio-wide)
    call the same knowledge once a specific property is being discussed,
    without paying to repeat it for all 15 properties on every turn (see
    build_lead_system_prompt's comment on why it's omitted there upfront).
    """
    # Imported here, not at module level -- avoids a system_prompt <-> faq_service
    # import cycle (system_prompt.py doesn't import faq_service, so this
    # direction is safe, but keeping the import local makes that non-obvious
    # safety explicit rather than accidental).
    from app.prompts.system_prompt import _active_seasonal_notes

    parts = [
        f"Check-in time: {property_.check_in_time}. Check-out time: {property_.check_out_time}. "
        f"Max guests: {property_.max_guests}."
    ]
    if property_.usp:
        parts.append(f"Description: {property_.usp}")
    if property_.house_rules:
        parts.append(f"House rules: {property_.house_rules}")
    if property_.neighborhood_info:
        parts.append(f"Neighborhood info: {property_.neighborhood_info}")
    if property_.amenities:
        parts.append(f"Amenities: {', '.join(property_.amenities)}")
    active_notes = _active_seasonal_notes(property_.seasonal_notes)
    if active_notes:
        parts.append(f"Currently in effect: {'; '.join(active_notes)}")
    return " | ".join(parts)


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
    # Knowledge Memory (memory-architecture-plan.md section 3.2) -- set only
    # when this gap's embedding is semantically close (see
    # embedding_service.SEMANTIC_MATCH_THRESHOLD) to an already-verified
    # FaqEntry's question. Computed live from stored embeddings (no external
    # API call at read time -- see embedding_service.py's module docstring),
    # never persisted; a host approving it just calls the existing
    # answer_faq_gap with suggested_answer pre-filled, same write path as a
    # manually-typed answer.
    suggested_faq_entry_id: uuid.UUID | None = None
    suggested_answer: str | None = None
    match_score: float | None = None


async def list_faq_gaps(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_id: uuid.UUID | None = None,
    date_range: DateRange | None = None,
) -> list[FaqGap]:
    """Groups pending UnansweredQuestion rows by normalized_question,
    ranked by frequency. property_id/date_range narrow which occurrences
    count, but every group's displayed question/property/timestamp comes
    from its most recent occurrence.

    Two further passes fold in Knowledge Memory (memory-architecture-plan.md
    section 3.1/3.2), both computed in Python over stored embeddings only --
    never a live embedding API call on this read path:
      1. Groups whose representative embeddings are semantically close to
         EACH OTHER (e.g. "is there parking" / "where can I park", still
         two different normalized_question values) are merged into one
         displayed gap, counts summed -- exact-text grouping alone misses
         these (see faq_service.py's own pg_trgm precedent for why
         character-level matching isn't enough).
      2. Each resulting gap is checked against the host's verified
         FaqEntry questions; a close match attaches a one-click
         `suggested_answer` instead of requiring the host to retype
         something they already answered under slightly different wording.
    """
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
            representative.c.question_embedding,
            counts_subq.c.count,
        )
        .join(counts_subq, counts_subq.c.normalized_question == representative.c.normalized_question)
        .order_by(counts_subq.c.count.desc())
    )
    rows = (await db.execute(stmt)).all()

    gaps = [
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
    embeddings_by_sample_id = {row.id: row.question_embedding for row in rows}
    gaps = _merge_semantically_similar_gaps(gaps, embeddings_by_sample_id)
    await _attach_suggested_answers(db, user_id, gaps, embeddings_by_sample_id)
    return gaps


def _merge_semantically_similar_gaps(
    gaps: list[FaqGap], embeddings_by_sample_id: dict[uuid.UUID, list | None]
) -> list[FaqGap]:
    """Section 3.1: two gaps with different normalized_question text but
    near-identical embeddings ("is there parking" / "where can I park") are
    folded into one displayed gap, counts summed, keeping whichever was
    asked more recently as the surfaced representative. Already sorted by
    count descending on entry, so earlier items in the list are merge
    targets for later, lower-count duplicates -- greedy, O(n^2) over a
    per-host gap list that's realistically dozens of rows, not thousands.
    """
    from app.services.embedding_service import SEMANTIC_MATCH_THRESHOLD, cosine_similarity

    merged: list[FaqGap] = []
    for gap in gaps:
        embedding = embeddings_by_sample_id.get(gap.sample_id)
        match = None
        if embedding is not None:
            for existing in merged:
                existing_embedding = embeddings_by_sample_id.get(existing.sample_id)
                if existing_embedding is not None and cosine_similarity(embedding, existing_embedding) >= SEMANTIC_MATCH_THRESHOLD:
                    match = existing
                    break
        if match is not None:
            match.count += gap.count
            if gap.last_asked_at > match.last_asked_at:
                match.question = gap.question
                match.last_asked_at = gap.last_asked_at
                match.sample_id = gap.sample_id
        else:
            merged.append(gap)
    merged.sort(key=lambda g: g.count, reverse=True)
    return merged


async def _attach_suggested_answers(
    db: AsyncSession,
    user_id: uuid.UUID,
    gaps: list[FaqGap],
    embeddings_by_sample_id: dict[uuid.UUID, list | None],
) -> None:
    """Section 3.2: mutates each gap in place with a suggested_answer if its
    embedding is close to one of the host's already-verified FaqEntry
    questions. No live embedding API call here -- only stored embeddings on
    both sides are compared."""
    from app.services.embedding_service import SEMANTIC_MATCH_THRESHOLD, cosine_similarity

    if not any(embeddings_by_sample_id.values()):
        return  # nothing to compare against without at least one embedding

    verified_entries = (
        await db.scalars(
            select(FaqEntry).where(
                FaqEntry.user_id == user_id,
                FaqEntry.status == "verified",
                FaqEntry.question_embedding.isnot(None),
            )
        )
    ).all()
    if not verified_entries:
        return

    for gap in gaps:
        embedding = embeddings_by_sample_id.get(gap.sample_id)
        if embedding is None:
            continue
        best_entry = None
        best_score = 0.0
        for entry in verified_entries:
            score = cosine_similarity(embedding, entry.question_embedding)
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry is not None and best_score >= SEMANTIC_MATCH_THRESHOLD:
            gap.suggested_faq_entry_id = best_entry.id
            gap.suggested_answer = best_entry.answer
            gap.match_score = round(best_score, 4)


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
        # Reuse the gap's own embedding (identical question text) instead of
        # a fresh API call, if one was already computed -- see
        # embedding_service.py. None here just means the fire-and-forget
        # backfill below will compute it.
        question_embedding=gap.question_embedding,
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

    if entry.question_embedding is None:
        asyncio.create_task(embedding_service.backfill_faq_entry_embedding(entry.id, entry.question))

    return entry
