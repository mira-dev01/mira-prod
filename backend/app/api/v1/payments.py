import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import payment_service
from app.services.payment_service import PaymentError

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("")
async def list_payments(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """The host's payment links and date holds, newest first."""
    return await payment_service.list_requests(db, current_user.id)


@router.post("/{request_id}/cancel")
async def cancel_payment(request_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Give up a hold: closes the link and frees the dates (waiting guests are told)."""
    try:
        await payment_service.cancel_request(db, current_user, request_id)
    except PaymentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"cancelled": True}
