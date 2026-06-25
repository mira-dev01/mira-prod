from datetime import date, timedelta

from app.models.pricing_rule import PricingRule
from app.services.pricing_engine import calculate_price, negotiate_rate


def _next_weekday(start: date, weekday: int) -> date:
    """weekday: Monday=0 ... Sunday=6"""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


async def test_weekend_surge_applied(test_property, db_session):
    monday = _next_weekday(date.today(), 0)
    friday = monday + timedelta(days=4)
    sunday = friday + timedelta(days=2)  # 2-night stay: Fri+Sat, both weekend nights

    breakdown = await calculate_price(db_session, test_property, friday, sunday)
    assert breakdown.nights == 2
    assert breakdown.weekend_nights == 2
    # weekend surge is 1.2x base_price by default
    assert breakdown.base_total == round(float(test_property.base_price) * 1.2 * 2, 2)


async def test_weekday_no_surge(test_property, db_session):
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    breakdown = await calculate_price(db_session, test_property, monday, wednesday)
    assert breakdown.weekend_nights == 0
    assert breakdown.base_total == round(float(test_property.base_price) * 2, 2)


async def test_length_of_stay_discount_applied(test_property, db_session):
    db_session.add(
        PricingRule(
            property_id=test_property.id,
            rule_type="length_of_stay",
            condition={"min_nights": 5},
            discount_percent=10,
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    six_nights_later = monday + timedelta(days=6)

    breakdown = await calculate_price(db_session, test_property, monday, six_nights_later, apply_discounts=True)
    assert breakdown.discount_percent == 10
    assert breakdown.discount_amount > 0

    no_discount = await calculate_price(db_session, test_property, monday, six_nights_later, apply_discounts=False)
    assert no_discount.discount_percent == 0


async def test_negotiate_rate_accepts_reasonable_offer(test_property, db_session):
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(db_session, test_property, monday, wednesday, guest_offer=999999, guest_loyalty="new")
    assert result.accepted is True


async def test_negotiate_rate_counters_lowball_offer(test_property, db_session):
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new")
    assert result.accepted is False
    assert result.counter_offer > 1
