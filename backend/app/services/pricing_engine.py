"""Tier 1 pricing (get_pricing) + a simple, rule-based Tier 3 negotiation
stub (negotiate_rate). The full PriceLabs/competitor-monitoring negotiation
engine described in the spec is Tier 3 (weeks 13-20) and intentionally not
built here — this gives the tool a real, useful response in the meantime.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.host_discount_rule import HostDiscountRule
from app.models.pricing_rule import PricingRule
from app.models.property import Property
from app.models.user import User

logger = logging.getLogger(__name__)

WEEKEND_WEEKDAYS = {4, 5, 6}  # Friday, Saturday, Sunday
MAX_NEGOTIATION_DISCOUNT_PERCENT = 15.0


@dataclass
class PriceBreakdown:
    nights: int
    base_total: float
    weekend_nights: int
    cleaning_fee: float
    tax_amount: float
    discount_percent: float
    discount_amount: float
    total: float
    per_night_avg: float


def _nightly_rate(base_price: float, day: date) -> float:
    if day.weekday() in WEEKEND_WEEKDAYS:
        return round(base_price * settings.weekend_surge_multiplier, 2)
    return base_price


async def _length_of_stay_discount_percent(db: AsyncSession, property_id: uuid.UUID, nights: int) -> float:
    rules = (
        await db.scalars(
            select(PricingRule).where(
                PricingRule.property_id == property_id,
                PricingRule.rule_type == "length_of_stay",
                PricingRule.active.is_(True),
            )
        )
    ).all()

    best = 0.0
    for rule in rules:
        min_nights = rule.condition.get("min_nights") if isinstance(rule.condition, dict) else None
        if min_nights is not None and nights >= min_nights:
            best = max(best, float(rule.discount_percent))
    return best


async def calculate_price(
    db: AsyncSession,
    property_: Property,
    check_in: date,
    check_out: date,
    apply_discounts: bool = True,
) -> PriceBreakdown:
    nights = (check_out - check_in).days
    base_price = float(property_.base_price)

    nightly_rates = [_nightly_rate(base_price, check_in + timedelta(days=i)) for i in range(nights)]
    base_total = round(sum(nightly_rates), 2)
    weekend_nights = sum(1 for d in nightly_rates if d != base_price)

    discount_percent = 0.0
    if apply_discounts:
        discount_percent = await _length_of_stay_discount_percent(db, property_.id, nights)
    discount_amount = round(base_total * discount_percent / 100, 2)

    cleaning_fee = float(settings.default_cleaning_fee_inr)
    taxable_amount = base_total - discount_amount + cleaning_fee
    tax_amount = round(taxable_amount * settings.default_tax_percent / 100, 2)

    total = round(base_total - discount_amount + cleaning_fee + tax_amount, 2)

    return PriceBreakdown(
        nights=nights,
        base_total=base_total,
        weekend_nights=weekend_nights,
        cleaning_fee=cleaning_fee,
        tax_amount=tax_amount,
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        total=total,
        per_night_avg=round(total / nights, 2) if nights else base_price,
    )


@dataclass
class NegotiationResult:
    accepted: bool
    counter_offer: float
    asking_price: float
    message: str
    refused: bool = False


@dataclass
class HostNegotiationPolicy:
    """Resolved, ready-to-use negotiation policy for one host -- either
    derived from their approved HostDiscountRule rows, or the untouched
    global defaults if the host has none / the lookup fails. Never
    constructed with a status other than "approved" rows -- see
    _get_host_negotiation_policy."""

    negotiation_allowed: bool
    max_discount_percent: float
    guest_requests_percent: float | None
    repeat_guest_percent: float | None


async def _get_host_negotiation_policy(db: AsyncSession, host_id: uuid.UUID | None) -> HostNegotiationPolicy:
    """Derive-on-read from the host's approved HostDiscountRule rows
    (memory-architecture-plan.md section 4.4) -- never materialized per
    property, so editing a host-level rule applies everywhere immediately.

    Mandatory fallback: any failure here (no host_id, DB error, no approved
    rows) returns today's exact pre-existing global-constant behavior.
    negotiate_rate must never error, hang, or silently default to a 0%/100%
    discount because of this lookup -- same "don't crash, don't block"
    discipline as BRIGHT_DATA_API_KEY/SMTP_* elsewhere in this codebase,
    since this runs live, mid-call, in the guest's negotiation path.
    """
    default_policy = HostNegotiationPolicy(
        negotiation_allowed=True,
        max_discount_percent=MAX_NEGOTIATION_DISCOUNT_PERCENT,
        guest_requests_percent=None,
        repeat_guest_percent=None,
    )
    if host_id is None:
        return default_policy

    try:
        host = await db.get(User, host_id)
        rules = (
            await db.scalars(
                select(HostDiscountRule).where(
                    HostDiscountRule.host_id == host_id,
                    HostDiscountRule.status == "approved",
                )
            )
        ).all()
    except Exception:
        logger.exception("Host negotiation policy lookup failed for host_id=%s -- using global defaults", host_id)
        return default_policy

    # host.negotiation_allowed is only ever None for an in-memory User that
    # was never flushed through the DB (server_default populates real rows)
    # -- treat that the same as "unset", i.e. allowed, not as "disabled".
    negotiation_allowed = True if host is None or host.negotiation_allowed is None else host.negotiation_allowed
    max_discount_percent = (
        float(host.max_discount_percent_override)
        if host is not None and host.max_discount_percent_override is not None
        else MAX_NEGOTIATION_DISCOUNT_PERCENT
    )

    guest_requests_percent = None
    repeat_guest_percent = None
    for rule in rules:
        percent = float(rule.discount_percent)
        if rule.trigger_type == "guest_requests":
            guest_requests_percent = percent if guest_requests_percent is None else max(guest_requests_percent, percent)
        elif rule.trigger_type == "repeat_guest_same_host":
            repeat_guest_percent = percent if repeat_guest_percent is None else max(repeat_guest_percent, percent)

    return HostNegotiationPolicy(
        negotiation_allowed=negotiation_allowed,
        max_discount_percent=max_discount_percent,
        guest_requests_percent=guest_requests_percent,
        repeat_guest_percent=repeat_guest_percent,
    )


async def negotiate_rate(
    db: AsyncSession,
    property_: Property,
    check_in: date,
    check_out: date,
    guest_offer: float | None,
    guest_loyalty: str = "new",
    host_id: uuid.UUID | None = None,
) -> NegotiationResult:
    breakdown = await calculate_price(db, property_, check_in, check_out, apply_discounts=False)
    asking_price = breakdown.total

    policy = await _get_host_negotiation_policy(db, host_id)

    if not policy.negotiation_allowed:
        return NegotiationResult(
            accepted=False,
            counter_offer=asking_price,
            asking_price=asking_price,
            refused=True,
            message=(
                f"I'm not able to offer a discount on {property_.name} -- ₹{asking_price:,.0f} "
                f"for {breakdown.nights} nights is our best price. I can connect you with the host "
                f"if you'd like to discuss further."
            ),
        )

    # trigger_type="repeat_guest_same_host" maps onto the existing
    # guest_loyalty argument ("returning"/"frequent") until Guest Memory
    # (memory-architecture-plan.md section 1) exists to check this
    # properly across a host's full portfolio -- guest_loyalty is already
    # the LLM-supplied signal for "this guest has stayed before," so this is
    # a safe, honest interim mapping, not a guess.
    is_repeat_guest = guest_loyalty in ("returning", "frequent")

    if is_repeat_guest and policy.repeat_guest_percent is not None:
        discount_percent = policy.repeat_guest_percent
    elif policy.guest_requests_percent is not None:
        discount_percent = policy.guest_requests_percent
    else:
        loyalty_bonus_percent = {"new": 0.0, "returning": 5.0, "frequent": 10.0}.get(guest_loyalty, 0.0)
        discount_percent = loyalty_bonus_percent + 10.0

    max_discount_percent = min(policy.max_discount_percent, discount_percent)
    floor_price = round(asking_price * (1 - max_discount_percent / 100), 2)

    if guest_offer is None:
        # Guest asked us to name a price rather than stating their own offer --
        # propose our best price directly instead of comparing against a number.
        return NegotiationResult(
            accepted=True,
            counter_offer=floor_price,
            asking_price=asking_price,
            message=(
                f"Best offer for {property_.name} ({breakdown.nights} nights): ₹{floor_price:,.0f} "
                f"(asking price ₹{asking_price:,.0f})."
            ),
        )

    if guest_offer >= floor_price:
        return NegotiationResult(
            accepted=True,
            counter_offer=guest_offer,
            asking_price=asking_price,
            message=f"Offer of ₹{guest_offer:,.0f} accepted for {property_.name} ({breakdown.nights} nights).",
        )

    return NegotiationResult(
        accepted=False,
        counter_offer=floor_price,
        asking_price=asking_price,
        message=(
            f"₹{guest_offer:,.0f} is below our floor for {property_.name}. "
            f"Counter-offer: ₹{floor_price:,.0f} for {breakdown.nights} nights "
            f"(asking price ₹{asking_price:,.0f})."
        ),
    )
