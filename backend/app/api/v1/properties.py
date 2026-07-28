import json
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.database import AsyncSessionLocal, get_db
from app.integrations import bright_data_client, cloudinary_client
from app.integrations.bright_data_client import BrightDataError
from app.models.property import Property
from app.models.user import User
from app.schemas.property import (
    AirbnbUrlImportRequest,
    AirbnbUrlImportStatus,
    AirbnbUrlImportTriggered,
    PropertyCreate,
    PropertyGalleryOut,
    PropertyImportResult,
    PropertyOut,
    PropertyUpdate,
)
from app.services import faq_service
from app.services.airbnb_import import parse_airbnb_listing, parse_bright_data_listing
from app.services.amenity_taxonomy import canonicalize_amenities
from app.services.calendar_service import sync_property_ical
from app.services.property.chunking import sync_property_chunks
from app.services.property_normalizer import (
    NAME_DELIMITER_RE,
    normalize_property_name,
    normalize_property_name_llm_fallback,
)

router = APIRouter(prefix="/properties", tags=["properties"])

_ROOM_ID_RE = re.compile(r"/rooms/(\d+)")
_TRAILING_COUNT_RE = re.compile(r"\b\d+\s*(?:bhk|bedrooms?|beds?|bathrooms?|baths?)\b", re.IGNORECASE)


def _brand_candidate(raw_name: str) -> str | None:
    """Best-guess brand/collection segment from a raw title. Real titles
    from both import sources consistently put the brand/collection name
    LAST ("Nile w/pool - Pause Project 1bhk", "Pine - Suite | Pause
    Project") -- the first segment is the actual listing name (see
    property_normalizer.normalize_property_name's own reasoning for the
    same ordering assumption), so the last remaining delimited segment,
    with any trailing bhk/bedroom/bathroom count stripped, is the best
    single-title guess at a brand. Used only for co-occurrence matching
    across a host's other properties (_resolve_brand below) -- never
    written directly as a confident brand from a single title alone."""
    segments = [s.strip(" -·|,") for s in NAME_DELIMITER_RE.split(raw_name) if s.strip(" -·|,")]
    if len(segments) < 2:
        return None
    candidate = _TRAILING_COUNT_RE.sub("", segments[-1]).strip(" -·|,")
    if not candidate or len(candidate.split()) > 4:
        return None
    # Property.brand is String(80) -- guard against a rare pathological
    # segment (4 very long words) still overflowing the column, same
    # class of bug as property_normalizer's own length clamping.
    return candidate[:80].strip(" -·|,") or None


async def _resolve_brand(db: AsyncSession, user_id: uuid.UUID, raw_name: str) -> str | None:
    """Cross-property co-occurrence check: if this title's brand-candidate
    segment matches an already-stored brand (or another property's own
    brand-candidate) for the same host, treat it as a confirmed brand.
    Deliberately simple (exact match on the candidate string, not a
    clustering algorithm) -- matches this codebase's preference for
    pragmatic heuristics over speculative sophistication."""
    candidate = _brand_candidate(raw_name)
    if not candidate:
        return None

    existing = list(
        (
            await db.scalars(
                select(Property).where(Property.user_id == user_id, Property.raw_name.isnot(None))
            )
        ).all()
    )
    for other in existing:
        if other.brand and other.brand.lower() == candidate.lower():
            return other.brand
        if other.raw_name and _brand_candidate(other.raw_name) == candidate and other.id:
            return candidate
    return None


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Property]:
    return list(
        (await db.scalars(select(Property).where(Property.user_id == current_user.id))).all()
    )


async def _upsert_property_from_parsed(
    user_id: uuid.UUID, listing_id: str, label: str, parsed: dict
) -> PropertyImportResult:
    """Shared create/update/FAQ-sync logic behind both Airbnb import paths --
    the bulk JSON-file upload below and the Bright Data URL import
    (import_airbnb_urls_status). Matches existing properties by
    airbnb_listing_id so re-importing the same listing updates rather than
    duplicates. Own DB session per call: reusing one shared session across a
    batch left it unusable after the first failure (SQLAlchemy's async
    session doesn't reliably recover mid-request after a rollback when more
    work follows in the same request) -- one bad record shouldn't take the
    rest of the batch down with it."""
    fields = parsed["fields"]
    faq_entries = parsed["faq_entries"]
    if not fields.get("name"):
        return PropertyImportResult(filename=label, status="error", error="Could not find a listing name")

    # confidence is set by normalize_property_name (the heuristic pass in
    # both parsers) but isn't itself a Property column -- pop it before the
    # fields dict is spread onto the model below.
    confidence = fields.pop("_normalizer_confidence", "high")

    async with AsyncSessionLocal() as db:
        if confidence == "low":
            fallback = await normalize_property_name_llm_fallback(
                fields.get("raw_name", fields["name"]), fields.get("usp")
            )
            if fallback is not None:
                if fallback.display_name and not fields.get("display_name"):
                    fields["display_name"] = fallback.display_name
                if fallback.spoken_name and not fields.get("spoken_name"):
                    fields["spoken_name"] = fallback.spoken_name
                if fallback.property_type and not fields.get("property_type"):
                    fields["property_type"] = fallback.property_type
                if fallback.property_style and not fields.get("property_style"):
                    fields["property_style"] = fallback.property_style
                if fallback.brand and not fields.get("brand"):
                    fields["brand"] = fallback.brand

        if not fields.get("brand") and fields.get("raw_name"):
            resolved_brand = await _resolve_brand(db, user_id, fields["raw_name"])
            if resolved_brand:
                fields["brand"] = resolved_brand

        existing = await db.scalar(
            select(Property).where(Property.user_id == user_id, Property.airbnb_listing_id == listing_id)
        )
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            await db.commit()
            await db.refresh(existing)
            faq_count = await faq_service.sync_imported_faq_entries(db, user_id, existing.id, faq_entries)
            await sync_property_chunks(db, existing)
            return PropertyImportResult(
                filename=label, status="updated", property=existing, faq_entries_created=faq_count
            )

        property_ = Property(user_id=user_id, airbnb_listing_id=listing_id, base_price=0, **fields)
        db.add(property_)
        await db.commit()
        await db.refresh(property_)
        faq_count = await faq_service.sync_imported_faq_entries(db, user_id, property_.id, faq_entries)
        await sync_property_chunks(db, property_)
        return PropertyImportResult(
            filename=label, status="created", property=property_, faq_entries_created=faq_count
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
            results.append(await _upsert_property_from_parsed(current_user.id, listing_id, filename, parsed))
        except Exception as exc:  # noqa: BLE001 - one bad file shouldn't fail the whole batch
            results.append(PropertyImportResult(filename=filename, status="error", error=str(exc)))

    return results


def _listing_id_from_url(url: str) -> str:
    match = _ROOM_ID_RE.search(url)
    return match.group(1) if match else url


@router.post("/import-airbnb-urls", response_model=AirbnbUrlImportTriggered)
async def import_airbnb_urls_trigger(
    payload: AirbnbUrlImportRequest, current_user: User = Depends(get_current_user)
) -> AirbnbUrlImportTriggered:
    """Starts an async Bright Data scrape job for the given Airbnb listing
    URLs (see app/integrations/bright_data_client.py -- Bright Data has no
    "all of this host's listings" mode, so the host pastes each listing URL
    individually rather than one profile link). Returns immediately with a
    snapshot_id; poll GET /import-airbnb-urls/{snapshot_id} for completion."""
    try:
        snapshot_id = await bright_data_client.trigger_scrape(payload.urls)
    except BrightDataError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return AirbnbUrlImportTriggered(snapshot_id=snapshot_id)


@router.get("/import-airbnb-urls/{snapshot_id}", response_model=AirbnbUrlImportStatus)
async def import_airbnb_urls_status(
    snapshot_id: str, current_user: User = Depends(get_current_user)
) -> AirbnbUrlImportStatus:
    """Poll endpoint for a Bright Data scrape job. While "running", the
    frontend should call this again after a short delay. Once "ready", each
    scraped listing is parsed and created/updated exactly like the JSON-file
    import path (parse_bright_data_listing + _upsert_property_from_parsed),
    matched by Airbnb's own room id so polling twice after completion is
    safe (updates, not duplicates)."""
    try:
        job_status = await bright_data_client.get_snapshot_status(snapshot_id)
    except BrightDataError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if job_status != "ready":
        return AirbnbUrlImportStatus(status=job_status)

    try:
        records = await bright_data_client.get_snapshot_data(snapshot_id)
    except BrightDataError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    results: list[PropertyImportResult] = []
    for record in records:
        url = record.get("url") or record.get("final_url") or ""
        listing_id = str(record.get("property_id") or _listing_id_from_url(url))
        label = record.get("listing_title") or record.get("name") or url or listing_id
        try:
            parsed = await parse_bright_data_listing(record, photo_folder=f"mira/properties/{current_user.id}")
            results.append(await _upsert_property_from_parsed(current_user.id, listing_id, label, parsed))
        except Exception as exc:  # noqa: BLE001 - one bad record shouldn't fail the whole batch
            results.append(PropertyImportResult(filename=label, status="error", error=str(exc)))

    return AirbnbUrlImportStatus(status="ready", results=results)


@router.post("", response_model=PropertyOut, status_code=status.HTTP_201_CREATED)
async def create_property(
    payload: PropertyCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Property:
    property_ = Property(
        user_id=current_user.id,
        **payload.model_dump(exclude={"faq"}),
        faq=[item.model_dump() for item in payload.faq],
        amenity_tags=canonicalize_amenities(payload.amenities),
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


@router.get("/{property_id}/gallery", response_model=PropertyGalleryOut)
async def get_property_gallery(property_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Property:
    """No-auth public endpoint backing the guest-facing photo gallery page
    (frontend /p/{property_id}/photos) -- the link the send_photos voice
    tool hands a guest over WhatsApp/email. Only exposes fields declared on
    PropertyGalleryOut; never reuse PropertyOut here."""
    property_ = await db.get(Property, property_id)
    if property_ is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return property_


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
    if "amenities" in updates:
        # amenity_tags (the canonical, filterable facet recommend_properties
        # queries) is derived from amenities -- must be recomputed here too,
        # not just at import time, or a host editing amenities via the
        # dashboard silently leaves the amenity filter matching against
        # stale tags.
        updates["amenity_tags"] = canonicalize_amenities(updates["amenities"] or [])
    for field, value in updates.items():
        setattr(property_, field, value)
    await db.commit()
    await db.refresh(property_)
    return property_


async def _renormalize_one(db: AsyncSession, property_: Property) -> None:
    """Re-runs the name normalizer against a property's own raw_name (or
    name, for pre-normalizer rows where raw_name is still unset) and
    overwrites the derived name fields. Shared by both the single and bulk
    renormalize endpoints below -- also what any future one-off backfill
    script should call directly, so there is exactly one code path for
    "recompute these fields," not a duplicate script-only version."""
    raw_name = property_.raw_name or property_.name
    normalized = normalize_property_name(raw_name, description=property_.usp)
    if normalized.confidence == "low":
        fallback = await normalize_property_name_llm_fallback(raw_name, property_.usp)
        if fallback is not None:
            # Regex-extracted bedroom/bathroom counts are more reliable
            # than an LLM's -- preserve the heuristic pass's counts rather
            # than letting the fallback's (always-None) ones win.
            fallback.bedroom_count = normalized.bedroom_count
            fallback.bathroom_count = normalized.bathroom_count
            normalized = fallback

    if not property_.raw_name:
        property_.raw_name = raw_name
    if normalized.display_name:
        property_.display_name = normalized.display_name
    if normalized.spoken_name:
        property_.spoken_name = normalized.spoken_name
    if normalized.property_type:
        property_.property_type = normalized.property_type
    if normalized.property_style:
        property_.property_style = normalized.property_style
    if normalized.bedroom_count is not None:
        property_.bedroom_count = normalized.bedroom_count
    if not property_.brand:
        resolved_brand = await _resolve_brand(db, property_.user_id, raw_name)
        if resolved_brand:
            property_.brand = resolved_brand


@router.post("/{property_id}/renormalize-name", response_model=PropertyOut)
async def renormalize_property_name(
    property_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Property:
    """Re-derives display_name/spoken_name/property_type/property_style/
    brand from this property's raw_name -- lets a host refresh a name the
    importer got wrong (e.g. after the normalizer's heuristics improve)
    without re-importing the whole listing. Backs a dashboard "Refresh
    name" action."""
    property_ = await get_owned_property(db, property_id, current_user)
    await _renormalize_one(db, property_)
    await db.commit()
    await db.refresh(property_)
    return property_


@router.post("/renormalize-names", response_model=list[PropertyOut])
async def renormalize_all_property_names(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[Property]:
    """Bulk version of the above, scoped to the current host's own
    properties only -- this is also the mechanism for backfilling
    display_name/spoken_name onto properties imported before this feature
    existed (raw_name is null for those; _renormalize_one falls back to
    `name`)."""
    properties = list(
        (await db.scalars(select(Property).where(Property.user_id == current_user.id))).all()
    )
    for property_ in properties:
        await _renormalize_one(db, property_)
    await db.commit()
    for property_ in properties:
        await db.refresh(property_)
    return properties


_MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10MB -- generous for a phone-camera photo, small enough to keep upload snappy
_ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


@router.post("/{property_id}/photos", response_model=PropertyOut)
async def add_property_photo(
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Property:
    """Host-driven photo upload for the property edit dialog -- distinct
    from the Airbnb/Bright Data import paths, which populate `photos` from
    scraped listing URLs. Appends the newly uploaded image to the end of the
    existing photos array rather than replacing it, so this can be called
    once per file as the host adds pictures one at a time."""
    if file.content_type not in _ALLOWED_PHOTO_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File must be an image (jpeg, png, webp, or heic)")

    data = await file.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image must be under 10MB")

    property_ = await get_owned_property(db, property_id, current_user)
    try:
        url = await cloudinary_client.upload_image_bytes(data, folder=f"mira/properties/{current_user.id}")
    except Exception as exc:  # noqa: BLE001 - surface as a clean 502 rather than a raw SDK/connection error
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Photo upload failed: {exc}") from exc

    property_.photos = [*property_.photos, url]
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
