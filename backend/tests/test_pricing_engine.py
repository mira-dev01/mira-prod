from datetime import date, timedelta

from app.integrations import redis_client
from app.integrations.searchapi_client import nightly_rate_cache_key
from app.models.pricing_rule import PricingRule
from app.services.pricing_engine import calculate_price, negotiate_rate


class _FakeRedis:
    """Same in-memory fake as tests/test_redis_client.py -- avoids needing
    a real Redis server just to test the cache-first branch in
    calculate_price."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value


def _install_fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "_client", fake)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    return fake


def _next_weekday(start: date, weekday: int) -> date:
    """weekday: Monday=0 ... Sunday=6"""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


async def test_flat_rate_no_weekend_surge(test_property, db_session):
    # No synthetic markup (weekend surge, cleaning fee, tax) for any
    # property -- base_total is always exactly base_price * nights,
    # weekday or weekend, until/unless a live Airbnb Smart Pricing fetch
    # overrides it (see test_exact_airbnb_pricing_falls_back_without_api_key
    # below for that path with no API key configured).
    monday = _next_weekday(date.today(), 0)
    friday = monday + timedelta(days=4)
    sunday = friday + timedelta(days=2)

    weekend = await calculate_price(db_session, test_property, friday, sunday)
    weekday = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))

    assert weekend.nights == 2
    assert weekend.base_total == round(float(test_property.base_price) * 2, 2)
    assert weekday.base_total == weekend.base_total


async def test_exact_airbnb_pricing_falls_back_to_base_price_without_api_key(test_property, db_session):
    # No SEARCHAPI_API_KEY configured in the test environment -- even with
    # exact_airbnb_pricing on and a listing id set, calculate_price must
    # never error or block; it falls back to the same flat base_price math
    # as every other property (see pricing_engine.calculate_price and
    # searchapi_client.fetch_property_coordinates/fetch_listing_total_price,
    # both of which return None with no key configured).
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "123456789"
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    breakdown = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))
    assert breakdown.base_total == round(float(test_property.base_price) * 2, 2)


async def test_calculate_price_uses_fully_cached_nightly_rates_with_no_api_key(test_property, db_session, monkeypatch):
    # No SEARCHAPI_API_KEY in the test environment (same as the
    # without-api-key test above) -- proves this path is genuinely served
    # from the Redis cache alone, since a live fetch would be impossible
    # here and would otherwise fall back to base_price instead.
    _install_fake_redis(monkeypatch)
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "123456789"
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    await redis_client.cache_set_json(nightly_rate_cache_key("123456789", monday), 4000.0, 3600)
    await redis_client.cache_set_json(nightly_rate_cache_key("123456789", monday + timedelta(days=1)), 4500.0, 3600)

    breakdown = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))
    assert breakdown.base_total == 8500.0
    assert breakdown.base_total != round(float(test_property.base_price) * 2, 2)


async def test_calculate_price_falls_back_when_nightly_cache_incomplete(test_property, db_session, monkeypatch):
    # Only ONE of the two required nights is cached -- must not quote a
    # partial total; falls through to the live-fetch path, which (no API
    # key in tests) lands on the same flat base_price as every other
    # property.
    _install_fake_redis(monkeypatch)
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "123456789"
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    await redis_client.cache_set_json(nightly_rate_cache_key("123456789", monday), 4000.0, 3600)
    # Second night deliberately left uncached.

    breakdown = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))
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
