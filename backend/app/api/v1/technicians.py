import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property, owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.technician import Technician
from app.models.user import User
from app.schemas.technician import TechnicianCreate, TechnicianOut

router = APIRouter(prefix="/technicians", tags=["technicians"])


@router.get("", response_model=list[TechnicianOut])
async def list_technicians(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Technician]:
    property_ids = await owned_property_ids(db, current_user)
    return list(
        (await db.scalars(select(Technician).where(Technician.property_id.in_(property_ids)))).all()
    )


@router.post("", response_model=TechnicianOut, status_code=status.HTTP_201_CREATED)
async def create_technician(
    payload: TechnicianCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Technician:
    await get_owned_property(db, payload.property_id, current_user)
    technician = Technician(**payload.model_dump())
    db.add(technician)
    await db.commit()
    await db.refresh(technician)
    return technician


@router.delete("/{technician_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_technician(
    technician_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    property_ids = await owned_property_ids(db, current_user)
    technician = await db.get(Technician, technician_id)
    if technician is None or technician.property_id not in property_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Technician not found")
    await db.delete(technician)
    await db.commit()
