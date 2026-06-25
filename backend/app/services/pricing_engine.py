"""Tier 1 pricing (get_pricing) + a simple, rule-based Tier 3 negotiation
stub (negotiate_rate). The full PriceLabs/competitor-monitoring negotiation
engine described in the spec is Tier 3 (weeks 13-20) and intentionally not
built here — this gives the tool a real, useful response in the meantime.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.pricing_rule import PricingRule
from app.models.property import Property

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


async def negotiate_rate(
    db: AsyncSession,
    property_: Property,
    check_in: date,
    check_out: date,
    guest_offer: float,
    guest_loyalty: str = "new",
) -> NegotiationResult:
    breakdown = await calculate_price(db, property_, check_in, check_out, apply_discounts=False)
    asking_price = breakdown.total

    loyalty_bonus_percent = {"new": 0.0, "returning": 5.0, "frequent": 10.0}.get(guest_loyalty, 0.0)
    max_discount_percent = min(MAX_NEGOTIATION_DISCOUNT_PERCENT, loyalty_bonus_percent + 10.0)
    floor_price = round(asking_price * (1 - max_discount_percent / 100), 2)

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
