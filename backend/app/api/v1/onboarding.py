from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.onboarding import FirstPropertyImportRequest, OnboardingOut, OnboardingProgressUpdate
from app.services import onboarding_service

# Business-profile fields aren't duplicated here -- the wizard saves them
# with the existing PATCH /auth/me, same as Settings and the Profile page.
router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("", response_model=OnboardingOut)
async def get_onboarding(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> OnboardingOut:
    return await onboarding_service.get_state(db, current_user)


@router.put("", response_model=OnboardingOut)
async def save_onboarding_progress(
    payload: OnboardingProgressUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingOut:
    try:
        return await onboarding_service.save_progress(db, current_user, payload)
    except onboarding_service.OnboardingError as exc:
        raise HTTPException(exc.status_code, exc.message)


@router.post("/first-property", response_model=OnboardingOut)
async def start_first_property_import(
    payload: FirstPropertyImportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingOut:
    """Starts (or, for the same URL, re-attaches to) the server-side import
    of the host's first Airbnb listing. The server finishes it even if the
    browser goes away; poll GET /onboarding for its status."""
    try:
        return await onboarding_service.start_first_property_import(db, current_user, payload)
    except onboarding_service.OnboardingError as exc:
        raise HTTPException(exc.status_code, exc.message)


@router.post("/complete", response_model=OnboardingOut)
async def complete_onboarding(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> OnboardingOut:
    try:
        return await onboarding_service.complete(db, current_user)
    except onboarding_service.OnboardingError as exc:
        raise HTTPException(exc.status_code, exc.message)
