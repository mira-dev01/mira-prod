import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.database import AsyncSessionLocal, get_db
from app.models.property import Property
from app.models.user import User
from app.schemas.property import PropertyCreate, PropertyImportResult, PropertyOut, PropertyUpdate
from app.services import faq_service
from app.services.airbnb_import import parse_airbnb_listing
from app.services.calendar_service import sync_property_ical

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Property]:
    return list(
        (await db.scalars(select(Property).where(Property.user_id == current_user.id))).all()
    )


@router.post("/import", response_model=list[PropertyImportResult])
async def import_properties(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
) -> list[PropertyImportResult]:
    """Bulk-create/update properties from scraped Airbnb listing JSON files
    (one file per listing). Matches existing properties by the filename --
    the Airbnb room id -- so re-uploading a refreshed scrape updates rather
    than duplicates.

    Each file gets its own database session. Reusing one shared session
    across the whole batch left it unusable after the first failure
    (SQLAlchemy's async session doesn't reliably recover mid-request after a
    rollback when more work follows in the same request) -- a bad file would
    take the rest of the batch down with it.

    Beyond the core property fields, the scrape also yields FAQ knowledge-base
    entries (neighbourhood highlights, bedroom/bed/bathroom counts,
    cancellation policy, Guest Favorite status, etc.) -- see
    app/services/airbnb_import.py. Those are synced into FaqEntry rows so the
    voice agent's search_faq tool can answer from them."""
    results: list[PropertyImportResult] = []

    for upload in files:
        filename = upload.filename or "unknown.json"
        listing_id = filename.rsplit(".", 1)[0]
        try:
            raw = json.loads(await upload.read())
            parsed = parse_airbnb_listing(raw)
            fields = parsed["fields"]
            faq_entries = parsed["faq_entries"]
            if not fields.get("name"):
                raise ValueError("Could not find a listing name in this file")

            async with AsyncSessionLocal() as db:
                existing = await db.scalar(
                    select(Property).where(
                        Property.user_id == current_user.id, Property.airbnb_listing_id == listing_id
                    )
                )
                if existing is not None:
                    for key, value in fields.items():
                        setattr(existing, key, value)
                    await db.commit()
                    await db.refresh(existing)
                    faq_count = await faq_service.sync_imported_faq_entries(
                        db, current_user.id, existing.id, faq_entries
                    )
                    results.append(
                        PropertyImportResult(
                            filename=filename, status="updated", property=existing, faq_entries_created=faq_count
                        )
                    )
                else:
                    property_ = Property(
                        user_id=current_user.id, airbnb_listing_id=listing_id, base_price=0, **fields
                    )
                    db.add(property_)
                    await db.commit()
                    await db.refresh(property_)
                    faq_count = await faq_service.sync_imported_faq_entries(
                        db, current_user.id, property_.id, faq_entries
                    )
                    results.append(
                        PropertyImportResult(
                            filename=filename, status="created", property=property_, faq_entries_created=faq_count
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - one bad file shouldn't fail the whole batch
            results.append(PropertyImportResult(filename=filename, status="error", error=str(exc)))

    return results


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
