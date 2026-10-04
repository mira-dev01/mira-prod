"""Internal /admin panel data API -- every route requires an admin session
(app/auth/admin.py::require_admin). Read-only except the billing-settings
PUT. All aggregation lives in app/services/admin_monitor_service.py."""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.database import get_db
from app.services import admin_monitor_service as monitor

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

MAX_RANGE_DAYS = 366


def admin_scope(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    include_test_calls: bool = Query(default=False),
) -> monitor.Scope:
    """start_date/end_date are inclusive UTC calendar days (same convention as
    app/api/v1/common.py::DateRange); default is the last 7 days -- the
    Phase 1A shadow-week window."""
    today = datetime.now(timezone.utc).date()
    end = end_date or today
    start = start_date or (end - timedelta(days=6))
    if start > end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="start_date is after end_date")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Date range is too large")
    return monitor.Scope(
        since=datetime.combine(start, time.min, tzinfo=timezone.utc),
        until=datetime.combine(end, time.min, tzinfo=timezone.utc) + timedelta(days=1),
        include_test_calls=include_test_calls,
    )


@router.get("/overview")
async def overview(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.overview(db, scope)


@router.get("/audio")
async def audio(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.audio(db, scope)


@router.get("/conversations")
async def conversations(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.conversations(db, scope)


@router.get("/performance")
async def performance(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.performance(db, scope)


@router.get("/leads")
async def leads(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.leads_and_recovery(db, scope)


@router.get("/hosts")
async def hosts(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.hosts(db, scope)


@router.get("/usage")
async def usage(scope: monitor.Scope = Depends(admin_scope), db: AsyncSession = Depends(get_db)) -> dict:
    return await monitor.usage(db, scope)


@router.get("/balances")
async def balances(refresh: bool = Query(default=False), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await monitor.balances(db, refresh=refresh)


@router.get("/settings/services")
async def list_service_settings(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await monitor.list_service_settings(db)


class ServiceSettingUpdate(BaseModel):
    prepaid_amount: float | None = Field(default=None, ge=0)
    clear_prepaid: bool = False
    unit_prices: dict[str, float | None] | None = None
    currency: str | None = Field(default=None, pattern=r"^(INR|USD)$")
    note: str | None = Field(default=None, max_length=500)


@router.put("/settings/services/{account}")
async def update_service_setting(
    account: str,
    body: ServiceSettingUpdate,
    admin_email: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if body.unit_prices is not None and any(v is not None and v < 0 for v in body.unit_prices.values()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Prices can't be negative")
    try:
        await monitor.update_service_setting(
            db,
            account,
            admin_email=admin_email,
            prepaid_amount=body.prepaid_amount,
            clear_prepaid=body.clear_prepaid,
            unit_prices=body.unit_prices,
            currency=body.currency,
            note=body.note,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown service")
    return await monitor.list_service_settings(db)
