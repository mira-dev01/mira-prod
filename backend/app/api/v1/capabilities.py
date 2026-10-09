from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.capability import CapabilityCatalogOut, CapabilityUpdate, HostCapabilitiesOut
from app.services import capability_service

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("/catalog", response_model=CapabilityCatalogOut)
async def get_catalog(current_user: User = Depends(get_current_user)) -> CapabilityCatalogOut:
    """The capability registry itself -- identical for every host."""
    return capability_service.catalog()


@router.get("", response_model=HostCapabilitiesOut)
async def list_my_capabilities(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> HostCapabilitiesOut:
    """Every capability's activation, readiness and blockers for the
    authenticated host -- always scoped to current_user, never a client-
    supplied host id."""
    return await capability_service.host_capabilities(db, current_user)


@router.patch("/{capability_id}", response_model=HostCapabilitiesOut)
async def update_my_capability(
    capability_id: str,
    payload: CapabilityUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HostCapabilitiesOut:
    """Enable/disable, record setup progress, or update capability-specific
    config. Returns the full list, since one change can alter another
    capability's state (dependencies)."""
    try:
        return await capability_service.update_capability(db, current_user, capability_id, payload)
    except capability_service.CapabilityNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown capability")
    except capability_service.CapabilityChangeBlockedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except capability_service.CapabilityConfigError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
