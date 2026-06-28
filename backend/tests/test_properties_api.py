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
