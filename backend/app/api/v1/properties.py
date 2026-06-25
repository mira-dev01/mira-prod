import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.property import Property
from app.models.user import User
from app.schemas.property import PropertyCreate, PropertyOut, PropertyUpdate
from app.services.calendar_service import sync_property_ical

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Property]:
    return list(
        (await db.scalars(select(Property).where(Property.user_id == current_user.id))).all()
    )


@router.post("", response_model=PropertyOut, status_code=status.HTTP_201_CREATED)
async def create_property(
    payload: PropertyCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Property:
    property_ = Property(
        user_id=current_user.id,
        **payload.model_dump(exclude={"faq"}),
        faq=[item.model_dump() for item in payload.faq],
    )
    db.add(property_)
    await db.commit()
    await db.refresh(property_)
    return property_


@router.get("/{property_id}", response_model=PropertyOut)
async def get_property(
    property_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Property:
    return await get_owned_property(db, property_id, current_user)


@router.patch("/{property_id}", response_model=PropertyOut)
async def update_property(
    property_id: uuid.UUID,
    payload: PropertyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Property:
    property_ = await get_owned_property(db, property_id, current_user)
    updates = payload.model_dump(exclude_unset=True)
    if "faq" in updates and updates["faq"] is not None:
        updates["faq"] = [item if isinstance(item, dict) else item.model_dump() for item in payload.faq]
    for field, value in updates.items():
        setattr(property_, field, value)
    await db.commit()
    await db.refresh(property_)
    return property_


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_property(
    property_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    property_ = await get_owned_property(db, property_id, current_user)
    await db.delete(property_)
    await db.commit()


@router.post("/{property_id}/sync-ical")
async def sync_ical(
    property_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    property_ = await get_owned_property(db, property_id, current_user)
    if not property_.ical_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Property has no ical_url configured")
    count = await sync_property_ical(db, property_)
    return {"synced_events": count}
