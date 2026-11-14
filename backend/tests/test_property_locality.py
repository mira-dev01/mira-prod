"""A property's locality and its matching key are always written together.

Confirmed live 2026-11-13: the host had a stay "in Indiranagar" and Mira said there was none, because the
area existed only inside the property name. `locality_key` is what search will match on (Task 12), so the
whole point is that every spelling a guest or a host might use lands on one key.
"""

import pytest

from app.models.property import Property
from app.services.property.locality import clean_locality, locality_key, set_locality


@pytest.mark.parametrize("spelling", ["Indiranagar", "Indira Nagar", "indiranagar ", "  INDIRA   NAGAR", "near Indiranagar"])
def test_every_spelling_of_one_area_has_the_same_key(spelling):
    assert locality_key(spelling) == "indiranagar"


def test_different_areas_have_different_keys():
    assert locality_key("Koramangala") != locality_key("Indiranagar")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_has_no_key(blank):
    assert locality_key(blank) is None
    assert clean_locality(blank) is None


def test_set_locality_writes_both_columns_and_keeps_the_hosts_spelling():
    prop = Property(name="x")
    set_locality(prop, "  Indira   Nagar ")
    assert prop.locality == "Indira Nagar"
    assert prop.locality_key == "indiranagar"


def test_a_blank_value_clears_both_columns():
    prop = Property(name="x", locality="Indiranagar", locality_key="indiranagar")
    set_locality(prop, "  ")
    assert prop.locality is None
    assert prop.locality_key is None


def test_a_name_with_no_english_letters_is_refused_not_stored_unfindable():
    prop = Property(name="x")
    with pytest.raises(ValueError, match="English letters"):
        set_locality(prop, "इंदिरानगर")
    assert prop.locality is None


@pytest.mark.asyncio
async def test_editing_a_locality_through_the_api_persists_and_moves_the_key(client, auth_headers, test_property):
    url = f"/api/v1/properties/{test_property.id}"

    first = await client.patch(url, json={"locality": "Indira Nagar"}, headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["locality"] == "Indira Nagar"

    second = await client.patch(url, json={"locality": "Koramangala"}, headers=auth_headers)
    assert second.status_code == 200

    reloaded = (await client.get(url, headers=auth_headers)).json()
    assert reloaded["locality"] == "Koramangala"


@pytest.mark.asyncio
async def test_api_stores_the_same_key_for_both_spellings(client, auth_headers, test_property, db_session):
    url = f"/api/v1/properties/{test_property.id}"

    await client.patch(url, json={"locality": "Indira Nagar"}, headers=auth_headers)
    await db_session.refresh(test_property)
    spaced = test_property.locality_key

    await client.patch(url, json={"locality": "Indiranagar"}, headers=auth_headers)
    await db_session.refresh(test_property)

    assert spaced == test_property.locality_key == "indiranagar"


@pytest.mark.asyncio
async def test_clearing_the_locality_through_the_api_clears_the_key(client, auth_headers, test_property, db_session):
    url = f"/api/v1/properties/{test_property.id}"
    await client.patch(url, json={"locality": "Indiranagar"}, headers=auth_headers)

    cleared = await client.patch(url, json={"locality": ""}, headers=auth_headers)

    assert cleared.status_code == 200
    await db_session.refresh(test_property)
    assert test_property.locality is None and test_property.locality_key is None


@pytest.mark.asyncio
async def test_a_devanagari_locality_is_a_422_with_a_readable_message(client, auth_headers, test_property):
    resp = await client.patch(f"/api/v1/properties/{test_property.id}", json={"locality": "इंदिरानगर"}, headers=auth_headers)

    assert resp.status_code == 422
    assert "English letters" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_with_a_locality_sets_the_key(client, auth_headers, db_session):
    resp = await client.post(
        "/api/v1/properties",
        json={"name": "Loft", "city": "Bengaluru", "locality": "Indira Nagar", "base_price": 5000},
        headers=auth_headers,
    )

    assert resp.status_code == 201
    created = await db_session.get(Property, __import__("uuid").UUID(resp.json()["id"]))
    assert created.locality_key == "indiranagar"
