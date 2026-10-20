"""Staff-engineer review finding: smart_pricing_service had zero test
coverage despite driving two of main.py's daily scheduled jobs
(_scheduled_smart_pricing_refresh, _scheduled_live_pricing_cache_refresh).
Covers the orchestration logic (city/listing grouping, dedup, unconfigured
short-circuit) with fetch_* calls monkeypatched -- no real SearchApi/Redis
network calls."""

import uuid

from app.services import smart_pricing_service as svc


async def test_refresh_smart_pricing_short_circuits_when_unconfigured(db_session, monkeypatch):
    monkeypatch.setattr(svc.settings, "searchapi_api_key", None)

    result = await svc.refresh_smart_pricing(db_session)

    assert result == {"cities_queried": 0, "properties_updated": 0, "errors": 0}


async def test_refresh_smart_pricing_updates_only_exact_airbnb_pricing_properties(
    db_session, test_property, monkeypatch
):
    monkeypatch.setattr(svc.settings, "searchapi_api_key", "test-key")
    test_property.exact_airbnb_pricing = True
    await db_session.commit()

    async def _fake_fetch_comparable_nightly_rates(city, check_in, check_out):
        assert city == "Goa"
        return [3000.0, 3200.0, 3400.0]

    monkeypatch.setattr(svc, "fetch_comparable_nightly_rates", _fake_fetch_comparable_nightly_rates)

    result = await svc.refresh_smart_pricing(db_session)

    assert result == {"cities_queried": 1, "properties_updated": 1, "errors": 0}
    await db_session.refresh(test_property)
    assert test_property.smart_price_estimate == 3200.0
    assert test_property.smart_price_sample_size == 3


async def test_refresh_smart_pricing_skips_properties_not_on_exact_airbnb_pricing(
    db_session, test_property, monkeypatch
):
    monkeypatch.setattr(svc.settings, "searchapi_api_key", "test-key")
    assert test_property.exact_airbnb_pricing is False

    calls = []

    async def _tracking_fetch(city, check_in, check_out):
        calls.append(city)
        return [3000.0]

    monkeypatch.setattr(svc, "fetch_comparable_nightly_rates", _tracking_fetch)

    result = await svc.refresh_smart_pricing(db_session)

    assert result == {"cities_queried": 0, "properties_updated": 0, "errors": 0}
    assert calls == []


async def test_refresh_smart_pricing_counts_a_failed_city_fetch_as_an_error(db_session, test_property, monkeypatch):
    monkeypatch.setattr(svc.settings, "searchapi_api_key", "test-key")
    test_property.exact_airbnb_pricing = True
    await db_session.commit()

    async def _raising_fetch(city, check_in, check_out):
        raise svc.SearchApiError("simulated request failure")

    monkeypatch.setattr(svc, "fetch_comparable_nightly_rates", _raising_fetch)

    result = await svc.refresh_smart_pricing(db_session)

    assert result == {"cities_queried": 1, "properties_updated": 0, "errors": 1}


async def test_refresh_live_pricing_cache_short_circuits_when_unconfigured(db_session, monkeypatch):
    monkeypatch.setattr(svc.settings, "searchapi_api_key", None)

    result = await svc.refresh_live_pricing_cache(db_session)

    assert result == {"listings_queried": 0, "nights_cached": 0, "errors": 0}


async def test_refresh_live_pricing_cache_dedupes_by_airbnb_listing_id(db_session, test_property, monkeypatch):
    """Two Property rows sharing one airbnb_listing_id must be treated as
    one listing -- coordinates fetched once, both rows backfilled. The
    uq_properties_user_airbnb_listing constraint only scopes uniqueness per
    host, so this uses a second host to reproduce the real "same Airbnb
    listing imported by two different hosts" case the module's own
    docstring describes."""
    from app.models.property import Property
    from app.models.user import User

    other_host = User(email=f"other-host-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}")
    db_session.add(other_host)
    await db_session.flush()

    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "listing-123"
    duplicate = Property(
        user_id=other_host.id,
        name="Test Villa (duplicate import)",
        city="Goa",
        exophone="+918099999999",
        base_price=4000,
        exact_airbnb_pricing=True,
        airbnb_listing_id="listing-123",
    )
    db_session.add(duplicate)
    monkeypatch.setattr(svc.settings, "searchapi_api_key", "test-key")
    await db_session.commit()

    coord_calls = []

    async def _fake_fetch_property_coordinates(listing_id):
        coord_calls.append(listing_id)
        return (15.5, 73.8)

    async def _fake_fetch_nightly_rate(latitude, longitude, listing_id, night):
        return 3000.0

    monkeypatch.setattr(svc, "fetch_property_coordinates", _fake_fetch_property_coordinates)
    monkeypatch.setattr(svc, "fetch_nightly_rate", _fake_fetch_nightly_rate)

    result = await svc.refresh_live_pricing_cache(db_session, days_ahead=1)

    assert coord_calls == ["listing-123"]
    assert result["listings_queried"] == 1
    assert result["nights_cached"] == 1
    await db_session.refresh(test_property)
    await db_session.refresh(duplicate)
    assert test_property.airbnb_latitude is not None
    assert duplicate.airbnb_latitude is not None


async def test_refresh_live_pricing_cache_counts_missing_coordinates_as_an_error(
    db_session, test_property, monkeypatch
):
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "listing-no-coords"
    monkeypatch.setattr(svc.settings, "searchapi_api_key", "test-key")
    await db_session.commit()

    async def _fake_fetch_property_coordinates(listing_id):
        return None

    monkeypatch.setattr(svc, "fetch_property_coordinates", _fake_fetch_property_coordinates)

    result = await svc.refresh_live_pricing_cache(db_session, days_ahead=1)

    assert result == {"listings_queried": 1, "nights_cached": 0, "errors": 1}


async def test_refresh_live_pricing_cache_does_not_count_an_unavailable_night_as_an_error(
    db_session, test_property, monkeypatch
):
    """A None result from fetch_nightly_rate means the night isn't
    bookable/found -- an expected outcome, not a bug (per the module's own
    docstring), so it must not inflate the errors count."""
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "listing-partial"
    test_property.airbnb_latitude = 15.5
    test_property.airbnb_longitude = 73.8
    monkeypatch.setattr(svc.settings, "searchapi_api_key", "test-key")
    await db_session.commit()

    async def _fake_fetch_nightly_rate(latitude, longitude, listing_id, night):
        return None

    monkeypatch.setattr(svc, "fetch_nightly_rate", _fake_fetch_nightly_rate)

    result = await svc.refresh_live_pricing_cache(db_session, days_ahead=2)

    assert result == {"listings_queried": 1, "nights_cached": 0, "errors": 0}
