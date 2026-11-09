"""A live pricing question must never make the guest wait on SearchApi for
more than settings.live_pricing_budget_seconds: past that it answers from the
static base_price (flagged as an estimate), while the lookup finishes in the
background and warms the cache for the guest's next question."""

import asyncio
import time
from datetime import date, timedelta

import pytest

from app.config import settings
from app.integrations import redis_client
from app.integrations.searchapi_client import nightly_rate_cache_key
from app.services import pricing_engine
from app.services.pricing_engine import calculate_price

CHECK_IN = date(2030, 1, 10)
CHECK_OUT = CHECK_IN + timedelta(days=2)


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "_client", fake)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    monkeypatch.setattr(settings, "live_pricing_budget_seconds", 0.2)
    pricing_engine._LIVE_INFLIGHT.clear()
    yield fake
    pricing_engine._LIVE_INFLIGHT.clear()


@pytest.fixture
def smart_property(test_property, db_session):
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "1234567890"
    test_property.base_price = 4000
    test_property.airbnb_latitude = 15.0
    test_property.airbnb_longitude = 73.9
    return test_property


def _install(monkeypatch, *, price_delay=0.0, price=9000.0, coords=(15.0, 73.9), coords_delay=0.0):
    calls = {"coords": 0, "price": 0}

    async def _coords(listing_id, timeout=15.0):
        calls["coords"] += 1
        await asyncio.sleep(coords_delay)
        return coords

    async def _price(latitude, longitude, listing_id, check_in, check_out, adults=2, timeout=15.0):
        calls["price"] += 1
        await asyncio.sleep(price_delay)
        return price

    monkeypatch.setattr(pricing_engine, "fetch_property_coordinates", _coords)
    monkeypatch.setattr(pricing_engine, "fetch_listing_total_price", _price)
    return calls


async def test_fast_live_lookup_is_used_and_not_flagged(smart_property, db_session, monkeypatch):
    _install(monkeypatch, price=9000.0)
    result = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)
    assert result.total == 9000.0
    assert result.is_estimate is False
    assert result.live_rate_unavailable is False


async def test_slow_lookup_returns_within_budget_with_static_estimate(smart_property, db_session, monkeypatch):
    _install(monkeypatch, price_delay=5.0)
    started = time.monotonic()
    result = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0  # budget is 0.2s -- nowhere near the old ~30s worst case
    assert result.total == 8000.0  # base_price 4000 x 2 nights
    assert result.is_estimate is True
    assert result.live_rate_unavailable is True


async def test_slow_coordinates_lookup_is_bounded_too(test_property, db_session, monkeypatch):
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "1234567890"
    test_property.base_price = 4000
    test_property.airbnb_latitude = None
    test_property.airbnb_longitude = None
    calls = _install(monkeypatch, coords_delay=5.0)

    started = time.monotonic()
    result = await calculate_price(db_session, test_property, CHECK_IN, CHECK_OUT)

    assert time.monotonic() - started < 1.0
    assert result.live_rate_unavailable is True
    assert calls["price"] == 0  # never got past the coordinates step in time


async def test_timed_out_lookup_finishes_in_background_and_warms_the_cache(
    smart_property, db_session, monkeypatch, _isolated
):
    cache = _isolated
    cached_prices: dict[str, float] = {}
    calls = _install(monkeypatch, price_delay=0.5, price=9000.0)

    # Mimic fetch_listing_total_price's own cache write on success, which the
    # replaced fake skips.
    async def _price(latitude, longitude, listing_id, check_in, check_out, adults=2, timeout=15.0):
        calls["price"] += 1
        await asyncio.sleep(0.5)
        cache.store[f"searchapi:listing_price:{listing_id}"] = "9000.0"
        cached_prices[listing_id] = 9000.0
        return 9000.0

    monkeypatch.setattr(pricing_engine, "fetch_listing_total_price", _price)

    first = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)
    assert first.live_rate_unavailable is True

    await asyncio.sleep(0.8)  # let the abandoned lookup land
    assert cached_prices == {"1234567890": 9000.0}
    assert calls["price"] == 1


async def test_repeat_question_joins_the_inflight_lookup_instead_of_paying_twice(
    smart_property, db_session, monkeypatch
):
    calls = _install(monkeypatch, price_delay=0.5, price=9000.0)

    first = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)
    assert first.live_rate_unavailable is True
    # Same question again, still inside the first lookup's lifetime: joins it.
    second = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)
    assert second.live_rate_unavailable is True
    assert calls["price"] == 1

    await asyncio.sleep(0.6)
    assert not pricing_engine._LIVE_INFLIGHT  # finished lookups are forgotten


async def test_lookup_error_is_a_clean_fallback_not_a_crash(smart_property, db_session, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("searchapi exploded")

    monkeypatch.setattr(pricing_engine, "fetch_listing_total_price", _boom)
    result = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)
    assert result.total == 8000.0
    assert result.live_rate_unavailable is True


async def test_cached_nightly_rates_still_answer_instantly(smart_property, db_session, monkeypatch, _isolated):
    for offset, rate in enumerate((3000.0, 3500.0)):
        _isolated.store[nightly_rate_cache_key("1234567890", CHECK_IN + timedelta(days=offset))] = str(rate)
    calls = _install(monkeypatch, price_delay=5.0)

    result = await calculate_price(db_session, smart_property, CHECK_IN, CHECK_OUT)

    assert result.total == 6500.0
    assert result.live_rate_unavailable is False
    assert calls == {"coords": 0, "price": 0}


async def test_non_smart_pricing_property_never_touches_the_live_path(test_property, db_session, monkeypatch):
    calls = _install(monkeypatch)
    test_property.exact_airbnb_pricing = False
    result = await calculate_price(db_session, test_property, CHECK_IN, CHECK_OUT)
    assert result.live_rate_unavailable is False
    assert calls == {"coords": 0, "price": 0}


async def test_newly_learned_coordinates_are_saved_on_the_property(test_property, db_session, monkeypatch):
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "1234567890"
    test_property.airbnb_latitude = None
    test_property.airbnb_longitude = None
    _install(monkeypatch, coords=(15.5, 73.8), price=9000.0)

    await calculate_price(db_session, test_property, CHECK_IN, CHECK_OUT)

    await db_session.refresh(test_property)
    assert float(test_property.airbnb_latitude) == 15.5
    assert float(test_property.airbnb_longitude) == 73.8


async def test_negotiation_off_a_timed_out_lookup_is_flagged_and_returns_fast(
    smart_property, db_session, monkeypatch
):
    _install(monkeypatch, price_delay=5.0)
    started = time.monotonic()
    result = await pricing_engine.negotiate_rate(
        db_session, smart_property, CHECK_IN, CHECK_OUT, guest_offer=7000.0, guest_loyalty="new"
    )
    assert time.monotonic() - started < 1.0
    assert result.is_estimate is True
    assert result.live_rate_unavailable is True


async def test_coordinates_learned_after_a_timeout_are_still_saved(test_property, db_session, monkeypatch):
    """The lookup outlives the call that started it; when it finally lands,
    the coordinates it learned must be saved so the next question skips that
    step. (Regression: the saver used to read the result before it existed.)"""
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "1234567890"
    test_property.airbnb_latitude = None
    test_property.airbnb_longitude = None
    await db_session.commit()
    _install(monkeypatch, coords=(15.5, 73.8), coords_delay=0.4, price=9000.0)  # budget is 0.2s

    result = await calculate_price(db_session, test_property, CHECK_IN, CHECK_OUT)
    assert result.live_rate_unavailable is True

    for _ in range(40):  # up to ~2s for the background lookup + save
        await asyncio.sleep(0.05)
        await db_session.refresh(test_property)
        if test_property.airbnb_latitude is not None:
            break
    assert float(test_property.airbnb_latitude) == 15.5
    assert float(test_property.airbnb_longitude) == 73.8
