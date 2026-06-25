import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.models.user import User


async def get_owned_property(db: AsyncSession, property_id: uuid.UUID, user: User) -> Property:
    property_ = await db.get(Property, property_id)
    if property_ is None or property_.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    return property_


async def owned_property_ids(db: AsyncSession, user: User) -> list[uuid.UUID]:
    rows = (await db.scalars(select(Property.id).where(Property.user_id == user.id))).all()
    return list(rows)
