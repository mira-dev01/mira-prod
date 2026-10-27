from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.ui_preferences import (
    AnalyticsLayoutOut,
    AnalyticsLayoutUpdate,
    NavigationPreferencesOut,
    NavigationPreferencesUpdate,
    OverviewLayoutOut,
    OverviewLayoutUpdate,
)
from app.services import ui_preferences_service as prefs

# Dashboard layout only -- always the signed-in user's own preferences,
# never a client-supplied id. Feature activation stays in /capabilities.
router = APIRouter(prefix="/preferences", tags=["preferences"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, prefs.PreferenceValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if isinstance(exc, prefs.PreferenceConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


_HANDLED = (prefs.PreferenceValidationError, prefs.PreferenceConflictError, prefs.PreferencePersistenceError)


@router.get("/navigation", response_model=NavigationPreferencesOut)
async def get_navigation(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> NavigationPreferencesOut:
    return await prefs.get_navigation(db, current_user)


@router.put("/navigation", response_model=NavigationPreferencesOut)
async def save_navigation(
    payload: NavigationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NavigationPreferencesOut:
    try:
        return await prefs.save_navigation(db, current_user, payload)
    except _HANDLED as exc:
        raise _http_error(exc)


@router.delete("/navigation", response_model=NavigationPreferencesOut)
async def reset_navigation(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> NavigationPreferencesOut:
    return await prefs.reset_navigation(db, current_user)


@router.get("/overview-widgets", response_model=OverviewLayoutOut)
async def get_overview_layout(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> OverviewLayoutOut:
    return await prefs.get_overview_layout(db, current_user)


@router.put("/overview-widgets", response_model=OverviewLayoutOut)
async def save_overview_layout(
    payload: OverviewLayoutUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OverviewLayoutOut:
    try:
        return await prefs.save_overview_layout(db, current_user, payload)
    except _HANDLED as exc:
        raise _http_error(exc)


@router.delete("/overview-widgets", response_model=OverviewLayoutOut)
async def reset_overview_layout(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> OverviewLayoutOut:
    return await prefs.reset_overview_layout(db, current_user)


@router.get("/analytics-widgets", response_model=AnalyticsLayoutOut)
async def get_analytics_layout(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> AnalyticsLayoutOut:
    return await prefs.get_analytics_layout(db, current_user)


@router.put("/analytics-widgets", response_model=AnalyticsLayoutOut)
async def save_analytics_layout(
    payload: AnalyticsLayoutUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsLayoutOut:
    try:
        return await prefs.save_analytics_layout(db, current_user, payload)
    except _HANDLED as exc:
        raise _http_error(exc)


@router.delete("/analytics-widgets", response_model=AnalyticsLayoutOut)
async def reset_analytics_layout(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> AnalyticsLayoutOut:
    return await prefs.reset_analytics_layout(db, current_user)
