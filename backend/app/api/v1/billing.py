from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import billing_service, capability_service
from app.services.billing_service import BillingError

router = APIRouter(prefix="/billing", tags=["billing"])


class RazorpayKeys(BaseModel):
    key_id: str = Field(max_length=80)
    key_secret: str = Field(max_length=200)

    def __repr__(self) -> str:  # never let the secret reach a log line
        return f"RazorpayKeys(key_id={self.key_id!r}, key_secret=***)"

    __str__ = __repr__


class TemplateText(BaseModel):
    text: str = Field(max_length=billing_service.TEMPLATE_MAX + 200)


class NotifyHost(BaseModel):
    enabled: bool


async def _view(user: User) -> dict:
    """The Billing tab, plus whether the host has the Online payments
    capability switched on (Settings -> Features): connecting Razorpay does
    nothing for callers while it is off."""
    view = billing_service.public_view(user)
    view["capability_enabled"] = await capability_service.is_capability_enabled(user.id, "online_payments")
    return view


def _http(exc: BillingError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("")
async def get_billing(current_user: User = Depends(get_current_user)) -> dict:
    """The Billing tab. Never contains the Razorpay secret."""
    return await _view(current_user)


@router.put("/razorpay")
async def connect_razorpay(keys: RazorpayKeys, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Checks the keys with Razorpay, then stores them (secret encrypted)."""
    try:
        await billing_service.connect_razorpay(db, current_user, keys.key_id, keys.key_secret)
    except BillingError as exc:
        raise _http(exc)
    return await _view(current_user)


@router.delete("/razorpay")
async def disconnect_razorpay(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await billing_service.disconnect_razorpay(db, current_user)
    except BillingError as exc:
        raise _http(exc)
    return await _view(current_user)


@router.put("/templates/{name}")
async def save_template_draft(
    name: str, body: TemplateText, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """Saves an edit as a draft. Guests keep getting the approved wording until it is approved."""
    try:
        await billing_service.save_template_draft(db, current_user, name, body.text)
    except BillingError as exc:
        raise _http(exc)
    return await _view(current_user)


@router.post("/templates/{name}/approve")
async def approve_template(name: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await billing_service.approve_template(db, current_user, name)
    except BillingError as exc:
        raise _http(exc)
    return await _view(current_user)


@router.delete("/templates/{name}")
async def reset_template(name: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Back to Mira's standard wording."""
    try:
        await billing_service.reset_template(db, current_user, name)
    except BillingError as exc:
        raise _http(exc)
    return await _view(current_user)


@router.put("/notify-host")
async def set_notify_host(body: NotifyHost, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    await billing_service.set_notify_host(db, current_user, body.enabled)
    return await _view(current_user)
