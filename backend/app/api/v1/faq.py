import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils import background
from app.api.v1.common import DateRange, date_range_query, get_owned_property
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.faq_entry import FaqEntry
from app.models.user import User
from app.utils.rate_limit import transcribe_rate_limit
from app.schemas.faq_entry import FaqEntryCreate, FaqEntryOut, FaqEntryUpdate, FaqScopeIn
from app.schemas.faq_gap import FaqGapAnalyticsOut, FaqGapAnswerIn, FaqGapOut
from app.services import embedding_service, faq_service
from app.services.faq_service import FaqScopeError

router = APIRouter(prefix="/faq", tags=["faq"])


@router.get("", response_model=list[FaqEntryOut])
async def list_faq_entries(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[FaqEntry]:
    return await faq_service.list_faq_entries(db, current_user.id)


def _scope_http_error(exc: FaqScopeError) -> HTTPException:
    detail = str(exc)
    code = status.HTTP_404_NOT_FOUND if "not found" in detail else 422
    return HTTPException(code, detail)


@router.post("", response_model=FaqEntryOut, status_code=status.HTTP_201_CREATED)
async def create_faq_entry(
    payload: FaqEntryCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> FaqEntry:
    fields = payload.model_dump(exclude={"property_id", "property_ids"})
    if payload.property_ids:
        targets: list = faq_service.scope_targets(payload.property_ids, False) or []
        try:
            await faq_service.validate_owned_properties(db, current_user.id, targets)
        except FaqScopeError as exc:
            raise _scope_http_error(exc)
    else:
        if payload.property_id is not None:
            await get_owned_property(db, payload.property_id, current_user)
        targets = [payload.property_id]

    entries = await faq_service.create_scoped_entries(db, user_id=current_user.id, targets=targets, **fields)
    await db.commit()
    for entry in entries:
        await db.refresh(entry)
    await faq_service.resync_many(db, {entry.property_id for entry in entries})
    return entries[0]


@router.patch("/{faq_id}", response_model=FaqEntryOut)
async def update_faq_entry(
    faq_id: uuid.UUID,
    payload: FaqEntryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaqEntry:
    entry = await faq_service.get_owned_faq_entry(db, faq_id, current_user.id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ entry not found")

    if payload.property_id is not None:
        await get_owned_property(db, payload.property_id, current_user)

    previous_property_id = entry.property_id
    changes = payload.model_dump(exclude_unset=True)
    if entry.group_id is not None and changes.get("property_id", entry.property_id) != entry.property_id:
        # One logical FAQ spans several rows; moving a single row would
        # silently split it. Re-scope the whole thing instead.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This FAQ applies to several properties -- change its scope instead"
        )
    question_changed = "question" in changes and changes["question"] != entry.question
    for field, value in changes.items():
        setattr(entry, field, value)
    if question_changed:
        # The stored embedding describes the old wording -- clear it so
        # semantic gap matching never uses it, and recompute in the
        # background (same helper new entries use).
        entry.question_embedding = None

    siblings = await faq_service.update_group_fields(db, entry, changes) if entry.group_id else []
    await db.commit()
    await db.refresh(entry)
    await faq_service.resync_many(db, {entry.property_id, *(sibling.property_id for sibling in siblings)})
    if previous_property_id != entry.property_id:
        # Moved between properties (or to/from portfolio-wide): the old one's
        # facts still contain it.
        await faq_service.resync_property_facts(db, previous_property_id)
    if question_changed:
        for row in [entry, *siblings]:
            background.spawn(embedding_service.backfill_faq_entry_embedding(row.id, row.question))
    return entry


@router.put("/{faq_id}/scope", response_model=list[FaqEntryOut])
async def set_faq_entry_scope(
    faq_id: uuid.UUID,
    payload: FaqScopeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FaqEntry]:
    """Applies one FAQ to all properties or to an explicit set of them."""
    entry = await faq_service.get_owned_faq_entry(db, faq_id, current_user.id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ entry not found")
    try:
        return await faq_service.set_entry_scope(
            db, entry, property_ids=payload.property_ids, apply_to_all=payload.apply_to_all
        )
    except FaqScopeError as exc:
        raise _scope_http_error(exc)


@router.delete("/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq_entry(
    faq_id: uuid.UUID,
    group: bool = Query(default=False, description="Also remove this FAQ from every property it was applied to"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    entry = await faq_service.get_owned_faq_entry(db, faq_id, current_user.id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ entry not found")
    rows = await faq_service.get_entry_group(db, entry) if group else [entry]
    property_ids = {row.property_id for row in rows}
    for row in rows:
        await db.delete(row)
    await db.commit()
    await faq_service.resync_many(db, property_ids)


# --- FAQ Learning Engine: questions search_faq had no verified answer for ---
# (app/services/tool_handlers.py handle_search_faq logs these during real
# calls; grouped/ranked here so hosts can convert frequent gaps into real,
# verified FaqEntry rows.)


@router.get("/gaps", response_model=list[FaqGapOut])
async def list_faq_gaps(
    property_id: uuid.UUID | None = Query(default=None),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list:
    return [
        {
            "sample_id": gap.sample_id,
            "question": gap.question,
            "count": gap.count,
            "property_id": gap.property_id,
            "last_asked_at": gap.last_asked_at,
            "suggested_faq_entry_id": gap.suggested_faq_entry_id,
            "suggested_answer": gap.suggested_answer,
            "match_score": gap.match_score,
            "source": gap.source,
        }
        for gap in await faq_service.list_faq_gaps(db, current_user.id, property_id, date_range)
    ]


@router.get("/gaps/analytics", response_model=FaqGapAnalyticsOut)
async def faq_gaps_analytics(
    bucket: str = Query(default="week", pattern="^(week|month)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await faq_service.faq_gap_analytics(db, current_user.id, bucket)


@router.post("/gaps/{gap_id}/answer", response_model=FaqEntryOut, status_code=status.HTTP_201_CREATED)
async def answer_faq_gap(
    gap_id: uuid.UUID,
    payload: FaqGapAnswerIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaqEntry:
    gap = await faq_service.get_owned_unanswered_question(db, gap_id, current_user.id)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unanswered question not found")
    try:
        return await faq_service.answer_faq_gap(
            db,
            gap,
            payload.answer,
            payload.apply_to_property,
            "host_gap_answer",
            question=payload.question,
            property_ids=payload.property_ids,
            apply_to_all=payload.apply_to_all,
        )
    except FaqScopeError as exc:
        raise _scope_http_error(exc)


@router.post("/gaps/{gap_id}/answer-voice", response_model=FaqEntryOut, status_code=status.HTTP_201_CREATED)
async def answer_faq_gap_voice(
    gap_id: uuid.UUID,
    apply_to_property: bool = False,
    apply_to_all: bool = False,
    property_ids: list[uuid.UUID] = Query(default=[]),
    question: str | None = Query(default=None, max_length=500),
    audio: UploadFile = File(...),
    current_user: User = Depends(transcribe_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> FaqEntry:
    gap = await faq_service.get_owned_unanswered_question(db, gap_id, current_user.id)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unanswered question not found")

    answer_text = await faq_service.transcribe_gap_answer_audio(audio)
    if not answer_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not transcribe audio -- please try again or type the answer")

    try:
        return await faq_service.answer_faq_gap(
            db,
            gap,
            answer_text,
            apply_to_property,
            "host_gap_answer_voice",
            question=question,
            property_ids=property_ids,
            apply_to_all=apply_to_all,
        )
    except FaqScopeError as exc:
        raise _scope_http_error(exc)
