import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.property_pricing_rule import PropertyPricingRule
from app.models.user import User
from app.schemas.property_pricing_rule import (
    PricingPolicyParseRequest,
    PricingPolicyParseResponse,
    PropertyPricingRuleOut,
    PropertyPricingRuleUpdate,
)
from app.services import pricing_policy_service

router = APIRouter(prefix="/property-pricing-rules", tags=["property-pricing-rules"])


async def _get_owned_rule(db: AsyncSession, rule_id: uuid.UUID, host_id: uuid.UUID) -> PropertyPricingRule | None:
    return await db.scalar(
        select(PropertyPricingRule).where(PropertyPricingRule.id == rule_id, PropertyPricingRule.host_id == host_id)
    )


@router.get("", response_model=list[PropertyPricingRuleOut])
async def list_property_pricing_rules(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[PropertyPricingRule]:
    result = await db.scalars(
        select(PropertyPricingRule)
        .where(PropertyPricingRule.host_id == current_user.id)
        .order_by(PropertyPricingRule.created_at.desc())
    )
    return list(result.all())


@router.post("/parse", response_model=PricingPolicyParseResponse, status_code=status.HTTP_201_CREATED)
async def parse_pricing_policy(
    payload: PricingPolicyParseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PricingPolicyParseResponse:
    """Parses the host's free-text (typed or dictated) pricing policy into
    structured PropertyPricingRule drafts (status="pending_validation", no
    properties selected yet). Drafts do NOT affect live pricing until the
    host picks which properties each applies to and approves it (see PATCH
    /property-pricing-rules/{id})."""
    try:
        parsed = await pricing_policy_service.parse_pricing_policy_text(payload.pricing_policy_text)
    except pricing_policy_service.PricingPolicyParseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not parse pricing policy: {exc}") from exc

    if not parsed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Couldn't find any specific pricing rules in that text -- try describing e.g. a minimum-stay "
            "requirement, a length-of-stay discount, or a fee for early check-in/late checkout.",
        )

    rules = pricing_policy_service.build_pending_rules(current_user.id, payload.pricing_policy_text, parsed)
    db.add_all(rules)
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    return PricingPolicyParseResponse(rules=rules)


@router.patch("/{rule_id}", response_model=PropertyPricingRuleOut)
async def update_property_pricing_rule(
    rule_id: uuid.UUID,
    payload: PropertyPricingRuleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PropertyPricingRule:
    """The host's property-selection + approve/edit/reject action. Setting
    status="approved" is what makes pricing_engine.py start reading this
    rule -- property_ids must be a non-empty subset of the host's own
    properties for an approval to actually apply anywhere; approving with
    no properties selected is accepted (the rule just matches nothing yet)
    rather than blocked, since a host may want to approve the rule's
    substance first and pick properties in a separate step."""
    rule = await _get_owned_rule(db, rule_id, current_user.id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pricing rule not found")

    updates = payload.model_dump(exclude_unset=True)

    if updates.get("property_ids"):
        owned_ids = {str(pid) for pid in await owned_property_ids(db, current_user)}
        requested_ids = set(updates["property_ids"])
        if not requested_ids.issubset(owned_ids):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more selected properties were not found")

    if updates.get("status") == "approved" and "source" not in updates:
        # A host edit (any rule field changed alongside approval) is worth
        # distinguishing from an as-parsed approval, same "source" tracking
        # HostDiscountRule already uses.
        if any(field in updates for field in ("rule_type", "condition", "discount_percent", "label")):
            rule.source = "host_edited"

    for field, value in updates.items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_property_pricing_rule(
    rule_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    rule = await _get_owned_rule(db, rule_id, current_user.id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pricing rule not found")
    await db.delete(rule)
    await db.commit()
