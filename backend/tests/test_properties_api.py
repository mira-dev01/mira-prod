async def test_create_two_properties_with_blank_exophone(client, auth_headers):
    """Regression test: exophone is unique in the DB, so a blank "" used to
    collide between properties instead of being treated as "not set"."""
    payload = {"name": "P1", "base_price": 1000, "max_guests": 2, "exophone": ""}
    first = await client.post("/api/v1/properties", json=payload, headers=auth_headers)
    assert first.status_code == 201
    assert first.json()["exophone"] is None

    second = await client.post(
        "/api/v1/properties", json={**payload, "name": "P2"}, headers=auth_headers
    )
    assert second.status_code == 201
    assert second.json()["exophone"] is None


async def test_update_property_with_blank_fields_does_not_500(client, auth_headers, test_property):
    payload = {
        "name": test_property.name,
        "city": "",
        "exophone": "",
        "base_price": float(test_property.base_price),
        "ical_url": "",
        "house_rules": "",
        "amenities": [],
        "faq": [],
        "check_in_time": "14:00",
        "check_out_time": "11:00",
        "max_guests": test_property.max_guests,
    }
    resp = await client.patch(f"/api/v1/properties/{test_property.id}", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["exophone"] is None
    assert resp.json()["city"] == ""


async def test_update_property_all_fields(client, auth_headers, test_property):
    payload = {
        "name": "Renamed Villa",
        "city": "Anjuna",
        "exophone": "+918099900011",
        "base_price": 5500,
        "ical_url": "https://example.com/cal.ics",
        "house_rules": "No smoking indoors.",
        "amenities": ["Pool", "WiFi"],
        "faq": [{"question": "Parking?", "answer": "Yes."}],
        "check_in_time": "15:00",
        "check_out_time": "10:00",
        "max_guests": 6,
    }
    resp = await client.patch(f"/api/v1/properties/{test_property.id}", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed Villa"
    assert body["exophone"] == "+918099900011"
    assert body["amenities"] == ["Pool", "WiFi"]
    assert body["faq"] == [{"question": "Parking?", "answer": "Yes."}]
    assert body["check_in_time"] == "15:00"


async def test_renormalize_property_name_derives_clean_fields(client, auth_headers, db_session, test_user):
    from app.models.property import Property

    property_ = Property(
        user_id=test_user.id,
        name="Pine - Glasshouse Suite w/bathtub | Pause Project",
        base_price=4200,
        max_guests=3,
    )
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    resp = await client.post(f"/api/v1/properties/{property_.id}/renormalize-name", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["raw_name"] == "Pine - Glasshouse Suite w/bathtub | Pause Project"
    assert body["spoken_name"] == "Pine"
    assert body["property_type"] == "glasshouse"


async def test_renormalize_all_property_names_resolves_shared_brand(client, auth_headers, db_session, test_user):
    from app.models.property import Property

    first = Property(
        user_id=test_user.id,
        name="Nile w/pool & projector - Pause Project 1bhk",
        base_price=4298,
        max_guests=3,
    )
    second = Property(
        user_id=test_user.id,
        name="Terra - Glasshouse Studio w/pool - Pause Project",
        base_price=4498,
        max_guests=3,
    )
    db_session.add_all([first, second])
    await db_session.commit()

    resp = await client.post("/api/v1/properties/renormalize-names", headers=auth_headers)
    assert resp.status_code == 200
    bodies = {b["spoken_name"]: b for b in resp.json()}
    assert bodies["Nile w/pool & projector"]["brand"] == "Pause Project"
    assert bodies["Terra"]["brand"] == "Pause Project"


async def test_renormalize_scoped_to_own_properties_only(client, auth_headers, db_session, test_property):
    resp = await client.post(
        f"/api/v1/properties/{test_property.id}/renormalize-name",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


async def test_create_property_populates_amenity_tags(client, auth_headers):
    resp = await client.post(
        "/api/v1/properties",
        json={
            "name": "New Villa",
            "base_price": 3000,
            "max_guests": 2,
            "amenities": ["Private pool", "Wifi"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["amenities"] == ["Private pool", "Wifi"]


async def test_update_property_amenities_recomputes_amenity_tags_for_filtering(
    client, auth_headers, db_session, test_user
):
    # Regression: amenity_tags (the canonical facet recommend_properties
    # filters on) must be recomputed whenever `amenities` changes via the
    # dashboard PATCH endpoint -- not just at import time -- or the
    # required_amenities filter silently matches against stale tags.
    from app.models.property import Property
    from app.schemas.tool import RecommendPropertiesArgs
    from app.services import tool_handlers

    property_ = Property(
        user_id=test_user.id,
        name="Nile",
        base_price=4000,
        max_guests=3,
        amenities=["Wifi"],
        amenity_tags=["wifi"],
    )
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    resp = await client.patch(
        f"/api/v1/properties/{property_.id}",
        json={"amenities": ["Private pool", "Wifi"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    args = RecommendPropertiesArgs(required_amenities=["pool"])
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    assert any(card.spoken_name == "Nile" for card in result.options)
