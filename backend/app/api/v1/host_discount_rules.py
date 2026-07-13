import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.host_discount_rule import HostDiscountRule
from app.models.user import User
from app.schemas.host_discount_rule import (
    DiscountPolicyParseRequest,
    DiscountPolicyParseResponse,
    HostDiscountRuleOut,
    HostDiscountRuleUpdate,
)
from app.services import discount_policy_service

router = APIRouter(prefix="/host-discount-rules", tags=["host-discount-rules"])


async def _get_owned_rule(db: AsyncSession, rule_id: uuid.UUID, host_id: uuid.UUID) -> HostDiscountRule | None:
    return await db.scalar(
        select(HostDiscountRule).where(HostDiscountRule.id == rule_id, HostDiscountRule.host_id == host_id)
    )


@router.get("", response_model=list[HostDiscountRuleOut])
async def list_host_discount_rules(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[HostDiscountRule]:
    result = await db.scalars(
        select(HostDiscountRule)
        .where(HostDiscountRule.host_id == current_user.id)
        .order_by(HostDiscountRule.created_at.desc())
    )
    return list(result.all())


@router.post("/parse", response_model=DiscountPolicyParseResponse, status_code=status.HTTP_201_CREATED)
async def parse_discount_policy(
    payload: DiscountPolicyParseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DiscountPolicyParseResponse:
    """Parses the host's free-text discount policy into structured
    HostDiscountRule drafts (status="pending_validation"). Also saves the
    raw text onto User.discount_policy_text so it's there to re-edit/re-parse
    later. Drafts do NOT affect live pricing until approved in the AI
    Training tab (see PATCH /host-discount-rules/{id})."""
    try:
        parsed = await discount_policy_service.parse_discount_policy_text(payload.discount_policy_text)
    except discount_policy_service.DiscountPolicyParseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not parse discount policy: {exc}") from exc

    if not parsed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Couldn't find any specific discount rules in that text -- try describing e.g. what discount "
            "you offer when a guest asks for one, or for repeat guests.",
        )

    current_user.discount_policy_text = payload.discount_policy_text
    rules = discount_policy_service.build_pending_rules(current_user.id, payload.discount_policy_text, parsed)
    db.add_all(rules)
    await db.commit()
    for rule in rules:
        await db.refresh(rule)

    return DiscountPolicyParseResponse(rules=rules)


@router.patch("/{rule_id}", response_model=HostDiscountRuleOut)
async def update_host_discount_rule(
    rule_id: uuid.UUID,
    payload: HostDiscountRuleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HostDiscountRule:
    """The host's approve/edit/reject action in the AI Training validation
    tab. Setting status="approved" is what makes pricing_engine start
    reading this rule; editing trigger_type/discount_percent while
    approving lets the host correct the AI's draft before it goes live."""
    rule = await _get_owned_rule(db, rule_id, current_user.id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Discount rule not found")

    updates = payload.model_dump(exclude_unset=True)
    if updates.get("status") == "approved" and "source" not in updates:
        # A host edit (trigger_type/discount_percent changed alongside
        # approval) is worth distinguishing from an as-parsed approval, for
        # the "source=host_policy vs manually added" badge on the /pricing
        # tab (see memory-architecture-plan.md section 4.4).
        if "trigger_type" in updates or "discount_percent" in updates:
            rule.source = "host_edited"

    for field, value in updates.items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host_discount_rule(
    rule_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    rule = await _get_owned_rule(db, rule_id, current_user.id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Discount rule not found")
    await db.delete(rule)
    await db.commit()
