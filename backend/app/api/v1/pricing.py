import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property, owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.pricing_rule import PricingRule
from app.models.user import User
from app.schemas.booking import AvailabilityQuery
from app.schemas.pricing_rule import PricingRuleCreate, PricingRuleOut
from app.services.pricing_engine import calculate_price

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.get("/rules", response_model=list[PricingRuleOut])
async def list_pricing_rules(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[PricingRule]:
    property_ids = await owned_property_ids(db, current_user)
    return list(
        (await db.scalars(select(PricingRule).where(PricingRule.property_id.in_(property_ids)))).all()
    )


@router.post("/rules", response_model=PricingRuleOut, status_code=status.HTTP_201_CREATED)
async def create_pricing_rule(
    payload: PricingRuleCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> PricingRule:
    await get_owned_property(db, payload.property_id, current_user)
    rule = PricingRule(**payload.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pricing_rule(
    rule_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    property_ids = await owned_property_ids(db, current_user)
    rule = await db.get(PricingRule, rule_id)
    if rule is None or rule.property_id not in property_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pricing rule not found")
    await db.delete(rule)
    await db.commit()


@router.post("/quote")
async def quote_price(
    payload: AvailabilityQuery, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """Dashboard-facing equivalent of the get_pricing tool, for hosts to preview rates."""
    property_ = await get_owned_property(db, payload.property_id, current_user)
    breakdown = await calculate_price(db, property_, payload.check_in, payload.check_out)
    return asdict(breakdown)
