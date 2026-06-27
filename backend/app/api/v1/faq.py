import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.faq_entry import FaqEntry
from app.models.user import User
from app.schemas.faq_entry import FaqEntryCreate, FaqEntryOut, FaqEntryUpdate
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
