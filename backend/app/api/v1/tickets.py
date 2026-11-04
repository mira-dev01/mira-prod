import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.ticket import TicketOut, TicketUpdate
from app.services import ticket_service

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    state: str = Query(default="open", pattern="^(open|resolved|all)$"),
    property_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Things Mira told guests would happen, still waiting on the host."""
    return await ticket_service.list_tickets(db, current_user.id, state, property_id)


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: uuid.UUID,
    payload: TicketUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark a ticket done or dismissed, or reopen it."""
    ticket = await ticket_service.get_owned_ticket(db, ticket_id, current_user.id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    await ticket_service.set_status(db, ticket, payload.status)
    rows = await ticket_service.list_tickets(db, current_user.id, "all", ticket_id=ticket.id)
    return rows[0]
