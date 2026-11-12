"""The per-property "weekend rate differs" toggle (any property, not only Spacez): Friday and Saturday
nights cost the weekend rate when it is on, every night costs the base rate when it is off, and the API
refuses a toggle that has no rate."""

from datetime import date, timedelta

from app.services.pricing_engine import calculate_price, evaluate_stay_prices


def _next(weekday: int) -> date:
    d = date.today() + timedelta(days=14)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


async def _turn_on(db, prop, price=6500):
    prop.weekend_rate_enabled, prop.weekend_price = True, price
    await db.commit()


async def test_off_by_default_so_weekend_nights_cost_the_base_rate(test_property, db_session):
    fri = _next(4)

    quote = await calculate_price(db_session, test_property, fri, fri + timedelta(days=2))

    assert test_property.weekend_rate_enabled is False
    assert quote.base_total == 2 * 4000


async def test_when_on_friday_and_saturday_nights_cost_the_weekend_rate(test_property, db_session):
    await _turn_on(db_session, test_property)
    thu = _next(3)

    fri_sat = await calculate_price(db_session, test_property, thu + timedelta(days=1), thu + timedelta(days=3))
    thu_fri = await calculate_price(db_session, test_property, thu, thu + timedelta(days=2))
    sun_mon = await calculate_price(db_session, test_property, thu + timedelta(days=3), thu + timedelta(days=5))

    assert fri_sat.base_total == 2 * 6500
    assert thu_fri.base_total == 4000 + 6500  # Thursday night at base, Friday night at the weekend rate
    assert sun_mon.base_total == 2 * 4000  # a Sunday night is not a weekend night


async def test_a_nights_only_quote_cannot_know_which_nights_so_it_stays_at_base(test_property, db_session):
    await _turn_on(db_session, test_property)

    quote = await calculate_price(db_session, test_property, None, None, nights=2)

    assert quote.base_total == 2 * 4000


async def test_recommendation_prices_use_the_weekend_rate_too(test_property, db_session):
    await _turn_on(db_session, test_property)
    fri = _next(4)

    prices = await evaluate_stay_prices(db_session, [test_property], None, fri, fri + timedelta(days=2))

    assert prices[test_property.id].total == 2 * 6500


async def test_the_api_refuses_a_weekend_toggle_with_no_rate(client, auth_headers):
    payload = {"name": "Toggle Villa", "base_price": 5000, "weekend_rate_enabled": True}

    refused = await client.post("/api/v1/properties", json=payload, headers=auth_headers)
    created = await client.post("/api/v1/properties", json={**payload, "weekend_price": 8000}, headers=auth_headers)

    assert refused.status_code == 422 and "weekend rate" in refused.json()["detail"]
    assert created.status_code == 201
    body = created.json()
    assert body["weekend_rate_enabled"] is True and float(body["weekend_price"]) == 8000

    turned_off = await client.patch(f"/api/v1/properties/{body['id']}", json={"weekend_rate_enabled": False}, headers=auth_headers)
    assert turned_off.status_code == 200 and turned_off.json()["weekend_rate_enabled"] is False
    no_rate = await client.patch(
        f"/api/v1/properties/{body['id']}", json={"weekend_rate_enabled": True, "weekend_price": 0}, headers=auth_headers
    )
    assert no_rate.status_code == 422


async def test_changing_the_weekend_rate_rebuilds_the_facts_of_a_spacez_villa_only(client, auth_headers, test_property, db_session, monkeypatch):
    """A Spacez villa states its rates in its knowledge base, so editing them must refresh it; an ordinary
    property's rate is not a knowledge-base fact and costs no rebuild."""
    from app.api.v1 import properties as properties_api

    calls = []

    async def spy(db, property_):
        calls.append(property_.id)

    monkeypatch.setattr(properties_api, "sync_property_facts", spy)
    body = {"weekend_rate_enabled": True, "weekend_price": 6000}
    await client.patch(f"/api/v1/properties/{test_property.id}", json=body, headers=auth_headers)
    assert calls == []  # ordinary property

    test_property.pricing_terms = {"base_guests": 4}
    await db_session.commit()
    await client.patch(f"/api/v1/properties/{test_property.id}", json={"weekend_price": 7000}, headers=auth_headers)
    assert calls == [test_property.id]  # a Spacez villa
