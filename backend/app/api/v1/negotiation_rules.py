import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.negotiation_rule import NegotiationRule
from app.models.user import User
from app.schemas.negotiation_rule import (
    NegotiationPolicyParseRequest,
    NegotiationPolicyParseResponse,
    NegotiationRuleOut,
    NegotiationRuleUpdate,
)
from app.services import negotiation_policy_service

router = APIRouter(prefix="/negotiation-rules", tags=["negotiation-rules"])


async def _get_owned_rule(db: AsyncSession, rule_id: uuid.UUID, host_id: uuid.UUID) -> NegotiationRule | None:
    return await db.scalar(
        select(NegotiationRule).where(NegotiationRule.id == rule_id, NegotiationRule.host_id == host_id)
    )


@router.get("", response_model=list[NegotiationRuleOut])
async def list_negotiation_rules(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[NegotiationRule]:
    result = await db.scalars(
        select(NegotiationRule)
        .where(NegotiationRule.host_id == current_user.id)
        .order_by(NegotiationRule.created_at.desc())
    )
    return list(result.all())


@router.post("/parse", response_model=NegotiationPolicyParseResponse, status_code=status.HTTP_201_CREATED)
async def parse_negotiation_policy(
    payload: NegotiationPolicyParseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NegotiationPolicyParseResponse:
    """Parses the host's free-text (typed or dictated) negotiation/pricing
    policy into structured NegotiationRule drafts (status="pending_validation").
    Also saves the raw text onto User.discount_policy_text so it's there to
    re-edit/re-parse later. Drafts do NOT affect live pricing/negotiation
    until approved in the AI Training tab (see PATCH /negotiation-rules/{id})."""
    try:
        parsed = await negotiation_policy_service.parse_negotiation_policy_text(payload.policy_text)
    except negotiation_policy_service.NegotiationPolicyParseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not parse negotiation policy: {exc}") from exc

    if not parsed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Couldn't find any specific rules in that text -- try describing e.g. what discount you offer "
            "when a guest asks, a minimum-stay requirement, or a fee for early check-in/late checkout.",
        )

    current_user.discount_policy_text = payload.policy_text
    rules = negotiation_policy_service.build_pending_rules(current_user.id, payload.policy_text, parsed)
    db.add_all(rules)
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    return NegotiationPolicyParseResponse(rules=rules)


@router.patch("/{rule_id}", response_model=NegotiationRuleOut)
async def update_negotiation_rule(
    rule_id: uuid.UUID,
    payload: NegotiationRuleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NegotiationRule:
    """The host's approve/edit/reject action in the AI Training validation
    tab. Setting status="approved" is what makes pricing_engine start
    reading this rule. property_ids only matters for the stay-pricing rule
    types (length_of_stay/minimum_stay_nights/early_checkin_fee/
    late_checkout_fee/custom) -- a discount_* trigger is host-wide by
    definition and approving one with no property_ids set still applies
    everywhere (see NegotiationRule's docstring on why empty means
    host-wide); a stay-pricing rule with no properties selected is accepted
    but simply matches nothing yet, same as the old PropertyPricingRule
    behavior."""
    rule = await _get_owned_rule(db, rule_id, current_user.id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Negotiation rule not found")

    # mode="json" (Phase 4D): stages, when present, is a list[NegotiationStage]
    # Pydantic model -- JSONB needs plain dicts, not model instances, or the
    # commit below fails to serialize. Every other field here was already a
    # JSON-primitive type, so this has no effect on them.
    updates = payload.model_dump(exclude_unset=True, mode="json")

    if updates.get("property_ids"):
        owned_ids = {str(pid) for pid in await owned_property_ids(db, current_user)}
        requested_ids = set(updates["property_ids"])
        if not requested_ids.issubset(owned_ids):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more selected properties were not found")

    if updates.get("status") == "approved" and "source" not in updates:
        # A host edit (any rule field changed alongside approval) is worth
        # distinguishing from an as-parsed approval, for the "edited by you"
        # badge in the reviewed-rules list. "stages" (Phase 4D) belongs in
        # this list for the same reason discount_percent already is -- both
        # are the substantive action value a host edits; self-review found
        # stages-only edits weren't marking host_edited, an inconsistency
        # with every other action-value field.
        if any(field in updates for field in ("rule_type", "condition", "discount_percent", "label", "stages")):
            rule.source = "host_edited"

    for field, value in updates.items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_negotiation_rule(
    rule_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    rule = await _get_owned_rule(db, rule_id, current_user.id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Negotiation rule not found")
    await db.delete(rule)
    await db.commit()
