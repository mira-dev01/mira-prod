import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.agent_config import AgentConfigOut, AgentConfigUpdate, PaymentOverride
from app.services import agent_config_service

router = APIRouter(prefix="/agent-config", tags=["agent-config"])


@router.get("", response_model=AgentConfigOut)
async def get_agent_config(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> AgentConfigOut:
    """The host's Agent rules (defaults filled in) and any per-property payment overrides."""
    return await agent_config_service.get_out(db, current_user)


@router.put("", response_model=AgentConfigOut)
async def update_agent_config(
    payload: AgentConfigUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> AgentConfigOut:
    """Replaces the sections sent; sections left out are untouched."""
    return await agent_config_service.update_config(db, current_user, payload)


@router.put("/properties/{property_id}/payment", response_model=AgentConfigOut)
async def set_property_payment_override(
    property_id: uuid.UUID,
    payload: PaymentOverride,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentConfigOut:
    """This property's payment rules differ from the portfolio's."""
    property_ = await get_owned_property(db, property_id, current_user)
    return await agent_config_service.set_payment_override(db, current_user, property_, payload)


@router.delete("/properties/{property_id}/payment", response_model=AgentConfigOut)
async def clear_property_payment_override(
    property_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> AgentConfigOut:
    """Back to following the portfolio payment rules."""
    property_ = await get_owned_property(db, property_id, current_user)
    return await agent_config_service.set_payment_override(db, current_user, property_, None)
