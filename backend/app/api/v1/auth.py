from datetime import datetime, timezone as dt_timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.integrations import bright_data_client
from app.integrations.bright_data_client import BrightDataError
from app.models.user import User
from app.schemas.user import HostRegistration, HostRegistrationResponse, Token, UserCreate, UserLogin, UserOut, UserUpdate
from app.services import faq_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> Token:
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.name,
        phone=payload.phone,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return Token(access_token=create_access_token(user.id))


@router.post("/register-host/transcribe-intro")
async def transcribe_registration_intro(audio: UploadFile = File(...)) -> dict:
    """Transcribes a prospective host's recorded voice agent intro (the
    "Add your voice agent's intro" registration step) to text, before an
    account exists -- deliberately unauthenticated, unlike
    /faq/gaps/{gap_id}/answer-voice which reuses the same
    transcribe_gap_answer_audio helper for an already-logged-in host.
    """
    text = await faq_service.transcribe_gap_answer_audio(audio)
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not transcribe audio -- please try again or type it instead")
    return {"text": text}


@router.post("/register-host", response_model=HostRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register_host(payload: HostRegistration, db: AsyncSession = Depends(get_db)) -> HostRegistrationResponse:
    """Fuller host onboarding: creates the account plus kicks off an Airbnb
    scrape for the one required property. Registration itself never waits on
    that scrape -- Bright Data's job can take a while and the account should
    exist regardless of scrape latency/failure (same "don't block, don't crash"
    stance as escalate_to_host's email fallback). The frontend gets a
    snapshot_id back and polls GET /properties/import-airbnb-urls/{snapshot_id},
    same as the existing "Import from Airbnb" dialog on the Properties page.

    Note the created property won't carry payload.ical_url yet -- that field
    only exists on Property, not User, and isn't known until the scrape
    resolves. The frontend is responsible for PATCHing it onto the created
    property once the poll returns a result.
    """
    existing_email = await db.scalar(select(User).where(User.email == payload.email))
    if existing_email is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    existing_exophone = await db.scalar(select(User).where(User.lead_exophone == payload.business_phone))
    if existing_exophone is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That business number is already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.name,
        phone=payload.phone,
        business_name=payload.business_name,
        lead_exophone=payload.business_phone,
        airbnb_host_status=payload.airbnb_host_status,
        property_count_estimate=payload.property_count_estimate,
        agent_first_message=payload.agent_first_message,
        terms_accepted_at=datetime.now(dt_timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    snapshot_id: str | None = None
    import_error: str | None = None
    try:
        snapshot_id = await bright_data_client.trigger_scrape([payload.airbnb_url])
    except BrightDataError as exc:
        # Account creation already committed above -- a scrape failure
        # (e.g. BRIGHT_DATA_API_KEY unset) shouldn't undo a successful
        # registration. Host can add the property manually afterward.
        import_error = str(exc)

    return HostRegistrationResponse(
        access_token=create_access_token(user.id),
        snapshot_id=snapshot_id,
        import_error=import_error,
    )


@router.post("/login", response_model=Token)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> Token:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> User:
    if payload.lead_exophone is not None and payload.lead_exophone != current_user.lead_exophone:
        existing = await db.scalar(select(User).where(User.lead_exophone == payload.lead_exophone))
        if existing is not None and existing.id != current_user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "That lead intake number is already in use")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)
    return current_user
