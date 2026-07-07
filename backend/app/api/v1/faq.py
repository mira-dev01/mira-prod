import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange, date_range_query, get_owned_property
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.faq_entry import FaqEntry
from app.models.user import User
from app.schemas.faq_entry import FaqEntryCreate, FaqEntryOut, FaqEntryUpdate
from app.schemas.faq_gap import FaqGapAnalyticsOut, FaqGapAnswerIn, FaqGapOut
from app.services import faq_service

router = APIRouter(prefix="/faq", tags=["faq"])


@router.get("", response_model=list[FaqEntryOut])
async def list_faq_entries(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[FaqEntry]:
    return await faq_service.list_faq_entries(db, current_user.id)


@router.post("", response_model=FaqEntryOut, status_code=status.HTTP_201_CREATED)
async def create_faq_entry(
    payload: FaqEntryCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> FaqEntry:
    if payload.property_id is not None:
        await get_owned_property(db, payload.property_id, current_user)

    entry = FaqEntry(user_id=current_user.id, **payload.model_dump())
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


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

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)

    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq_entry(
    faq_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    entry = await faq_service.get_owned_faq_entry(db, faq_id, current_user.id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ entry not found")
    await db.delete(entry)
    await db.commit()


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
    return await faq_service.answer_faq_gap(db, gap, payload.answer, payload.apply_to_property, "host_gap_answer")


@router.post("/gaps/{gap_id}/answer-voice", response_model=FaqEntryOut, status_code=status.HTTP_201_CREATED)
async def answer_faq_gap_voice(
    gap_id: uuid.UUID,
    apply_to_property: bool = False,
    audio: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaqEntry:
    gap = await faq_service.get_owned_unanswered_question(db, gap_id, current_user.id)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unanswered question not found")

    answer_text = await faq_service.transcribe_gap_answer_audio(audio)
    if not answer_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not transcribe audio -- please try again or type the answer")

    return await faq_service.answer_faq_gap(db, gap, answer_text, apply_to_property, "host_gap_answer_voice")
