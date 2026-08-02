from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.integrations import bright_data_client, cloudinary_client
from app.integrations.bright_data_client import BrightDataError
from app.models.user import User
from app.schemas.user import HostOnboarding, HostOnboardingResponse, UserOut, UserUpdate
from app.services import faq_service

router = APIRouter(prefix="/auth", tags=["auth"])

_MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10MB -- matches properties.py's own photo upload cap
_ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


@router.post("/register-host/transcribe-intro")
async def transcribe_registration_intro(audio: UploadFile = File(...)) -> dict:
    """Transcribes a prospective host's recorded voice agent intro (the
    "Add your voice agent's intro" onboarding step) to text. Unauthenticated
    -- reuses the same transcribe_gap_answer_audio helper as the logged-in
    /faq/gaps/{gap_id}/answer-voice path, just without the auth dependency,
    since the onboarding wizard may call this before the host has finished
    setting up their Clerk session.
    """
    text = await faq_service.transcribe_gap_answer_audio(audio)
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not transcribe audio -- please try again or type it instead")
    return {"text": text}


@router.post("/onboarding", response_model=HostOnboardingResponse, status_code=status.HTTP_200_OK)
async def onboard_host(
    payload: HostOnboarding,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HostOnboardingResponse:
    """Fills in the business/Airbnb-import fields Clerk's own sign-up form
    doesn't collect, called from the post-signup onboarding page once
    get_current_user has already resolved a real (Clerk-authenticated)
    User row. Also kicks off an Airbnb scrape for the one required
    property -- this never blocks on that scrape (Bright Data's job can
    take a while); the frontend gets a snapshot_id back and polls
    GET /properties/import-airbnb-urls/{snapshot_id}, same as the existing
    "Import from Airbnb" dialog on the Properties page.

    Note the created property won't carry payload.ical_url yet -- that
    field only exists on Property, not User, and isn't known until the
    scrape resolves. The frontend is responsible for PATCHing it onto the
    created property once the poll returns a result.
    """
    if payload.business_phone != current_user.lead_exophone:
        existing_exophone = await db.scalar(select(User).where(User.lead_exophone == payload.business_phone))
        if existing_exophone is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "That business number is already registered")

    current_user.name = payload.name
    current_user.phone = payload.phone
    current_user.business_name = payload.business_name
    current_user.lead_exophone = payload.business_phone
    current_user.airbnb_host_status = payload.airbnb_host_status
    current_user.property_count_estimate = payload.property_count_estimate
    current_user.agent_first_message = payload.agent_first_message
    await db.commit()

    snapshot_id: str | None = None
    import_error: str | None = None
    try:
        snapshot_id = await bright_data_client.trigger_scrape([payload.airbnb_url])
    except BrightDataError as exc:
        # Profile update already committed above -- a scrape failure (e.g.
        # BRIGHT_DATA_API_KEY unset) shouldn't undo it. Host can add the
        # property manually afterward.
        import_error = str(exc)

    return HostOnboardingResponse(snapshot_id=snapshot_id, import_error=import_error)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


async def _upload_host_image(file: UploadFile, current_user: User, db: AsyncSession) -> str:
    """Shared by /me/photo and /me/banner -- same Cloudinary upload pattern
    as POST /properties/{id}/photos, just replacing a single *_url field
    instead of appending to an array."""
    if file.content_type not in _ALLOWED_PHOTO_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File must be an image (jpeg, png, webp, or heic)")

    data = await file.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image must be under 10MB")

    try:
        return await cloudinary_client.upload_image_bytes(data, folder=f"mira/hosts/{current_user.id}")
    except Exception as exc:  # noqa: BLE001 - surface as a clean 502 rather than a raw SDK/connection error
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Photo upload failed: {exc}") from exc


@router.post("/me/photo", response_model=UserOut)
async def upload_my_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    current_user.photo_url = await _upload_host_image(file, current_user, db)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/banner", response_model=UserOut)
async def upload_my_banner(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    current_user.banner_url = await _upload_host_image(file, current_user, db)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> User:
    if payload.lead_exophone is not None and payload.lead_exophone != current_user.lead_exophone:
        existing = await db.scalar(select(User).where(User.lead_exophone == payload.lead_exophone))
        if existing is not None and existing.id != current_user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "That lead intake number is already in use")

    if payload.twilio_lead_number is not None and payload.twilio_lead_number != current_user.twilio_lead_number:
        existing_twilio = await db.scalar(
            select(User).where(User.twilio_lead_number == payload.twilio_lead_number)
        )
        if existing_twilio is not None and existing_twilio.id != current_user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "That Twilio lead number is already in use")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)
    return current_user
