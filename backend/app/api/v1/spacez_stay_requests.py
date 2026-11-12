"""SPACEZ ONLY. The team's yes / no on a guest's early check-in / late check-out request.

The request arrives as a ticket (kind "stay_request", Leads -> Tickets); these two endpoints are what its
Approve / Decline buttons call. All the logic is in app/services/spacez_stay_requests.py.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services import spacez_stay_requests

router = APIRouter(prefix="/spacez/stay-requests", tags=["spacez"])


async def _answer(action, ticket_id: uuid.UUID, user: User, db: AsyncSession) -> dict:
    try:
        request = await action(db, user, ticket_id)
    except spacez_stay_requests.StayRequestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return {"status": request.status}


@router.post("/{ticket_id}/approve")
async def approve(ticket_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Yes: sends the guest a payment link for the flat fee on WhatsApp."""
    return await _answer(spacez_stay_requests.approve, ticket_id, current_user, db)


@router.post("/{ticket_id}/decline")
async def decline(ticket_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    """No: tells the guest on WhatsApp that the team cannot do it."""
    return await _answer(spacez_stay_requests.decline, ticket_id, current_user, db)
