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


async def test_update_property_is_premium_round_trips_and_defaults_false(client, auth_headers, test_property):
    """Recommendation conversations ("Phase X"): is_premium is host-set via
    the property editor (PropertyUpdate), never LLM-inferred -- grounds
    "something more premium" requests in a real fact. Defaults False (an
    opt-in flag), and a PATCH toggling it must round-trip through PropertyOut."""
    get_resp = await client.get(f"/api/v1/properties/{test_property.id}", headers=auth_headers)
    assert get_resp.json()["is_premium"] is False

    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"is_premium": True}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["is_premium"] is True

    get_resp_2 = await client.get(f"/api/v1/properties/{test_property.id}", headers=auth_headers)
    assert get_resp_2.json()["is_premium"] is True


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


async def test_new_property_defaults_call_handling_to_mira(client, auth_headers):
    """Call Ownership Schedule, Phase 1: a freshly created property (which
    never specifies call_handling_mode -- it's not on PropertyCreate) must
    default to MIRA, preserving today's only real routing behavior."""
    resp = await client.post(
        "/api/v1/properties",
        json={"name": "New Villa", "base_price": 2000, "max_guests": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["call_handling_mode"] == "MIRA"
    assert body["call_handling_schedule_start"] is None
    assert body["call_handling_schedule_end"] is None
    assert body["timezone"] == "Asia/Kolkata"


async def test_existing_property_defaults_call_handling_to_mira(client, auth_headers, test_property):
    """The test_property fixture never sets call_handling_mode either --
    covers the "existing row created before this feature" case via the ORM
    default, same as a real pre-migration property would see after the
    migration's server_default backfill."""
    resp = await client.get(f"/api/v1/properties/{test_property.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_handling_mode"] == "MIRA"
    assert body["timezone"] == "Asia/Kolkata"


async def test_update_property_call_handling_mode_to_host(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "HOST"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["call_handling_mode"] == "HOST"

    # Round-trips on a subsequent GET, not just the PATCH response.
    get_resp = await client.get(f"/api/v1/properties/{test_property.id}", headers=auth_headers)
    assert get_resp.json()["call_handling_mode"] == "HOST"


async def test_update_property_call_handling_mode_back_to_mira(client, auth_headers, test_property):
    await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"call_handling_mode": "HOST"}, headers=auth_headers
    )
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"call_handling_mode": "MIRA"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["call_handling_mode"] == "MIRA"


async def test_update_property_call_handling_mode_rejects_invalid_value(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "SOMETHING_ELSE"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_update_property_schedule_same_day_window(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_schedule_start": "09:00", "call_handling_schedule_end": "22:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_handling_schedule_start"] == "09:00"
    assert body["call_handling_schedule_end"] == "22:00"


async def test_update_property_schedule_overnight_window_is_valid(client, auth_headers, test_property):
    """22:00 -> 06:00 (start > end) must remain a valid, storable value --
    the wraparound is interpreted by a later phase's resolver, not rejected
    here."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_schedule_start": "22:00", "call_handling_schedule_end": "06:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_handling_schedule_start"] == "22:00"
    assert body["call_handling_schedule_end"] == "06:00"


async def test_update_property_timezone(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"timezone": "Asia/Kolkata"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Asia/Kolkata"


async def test_update_property_does_not_require_call_handling_fields(client, auth_headers, test_property):
    """An unrelated PATCH (e.g. just renaming) must not be forced to also
    supply schedule fields -- PropertyUpdate's new fields are all optional
    and excluded via exclude_unset when omitted."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"name": "Renamed Only"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed Only"
    # Untouched -- still whatever the fixture/default left it as.
    assert body["call_handling_mode"] == "MIRA"


async def test_update_property_call_handling_mode_scheduled_with_window(client, auth_headers, test_property):
    """SCHEDULED is a real, distinct third state -- not inferred from a
    populated schedule while mode stays MIRA/HOST. Setting mode=SCHEDULED
    together with both schedule bounds in the same request must succeed
    and round-trip."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={
            "call_handling_mode": "SCHEDULED",
            "call_handling_schedule_start": "09:00",
            "call_handling_schedule_end": "17:00",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_handling_mode"] == "SCHEDULED"
    assert body["call_handling_schedule_start"] == "09:00"
    assert body["call_handling_schedule_end"] == "17:00"

    get_resp = await client.get(f"/api/v1/properties/{test_property.id}", headers=auth_headers)
    assert get_resp.json()["call_handling_mode"] == "SCHEDULED"


async def test_update_property_call_handling_mode_scheduled_overnight_window(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={
            "call_handling_mode": "SCHEDULED",
            "call_handling_schedule_start": "22:00",
            "call_handling_schedule_end": "06:00",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_handling_schedule_start"] == "22:00"
    assert body["call_handling_schedule_end"] == "06:00"


async def test_update_property_call_handling_mode_scheduled_without_window_is_rejected(
    client, auth_headers, test_property
):
    """The data-model fix: SCHEDULED with no window is meaningless -- there
    would be nothing for a future resolver to evaluate against. Must be
    rejected at the schema boundary, not silently accepted."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "SCHEDULED"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_update_property_call_handling_mode_scheduled_with_only_start_is_rejected(
    client, auth_headers, test_property
):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "SCHEDULED", "call_handling_schedule_start": "09:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_update_property_call_handling_mode_mira_and_host_do_not_require_schedule(
    client, auth_headers, test_property
):
    """MIRA/HOST are complete, unconditional configurations on their own --
    unlike SCHEDULED, they must not require a schedule window."""
    mira_resp = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"call_handling_mode": "MIRA"}, headers=auth_headers
    )
    assert mira_resp.status_code == 200

    host_resp = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"call_handling_mode": "HOST"}, headers=auth_headers
    )
    assert host_resp.status_code == 200


async def test_update_property_schedule_rejects_malformed_time(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_schedule_start": "9am"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_update_property_schedule_rejects_out_of_range_time(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_schedule_start": "25:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_update_property_timezone_rejects_invalid_iana_identifier(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"timezone": "Not/A_Real_Zone"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_update_property_timezone_accepts_other_iana_identifiers(client, auth_headers, test_property):
    """Explicitly not hard-coded to India -- any valid IANA zone works."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"timezone": "America/New_York"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/New_York"


async def test_update_property_call_handling_by_non_owner_is_rejected(client, db_session, test_property):
    """Phase 3: a host must not be able to change another host's property's
    call-ownership settings -- reuses get_owned_property's existing 404-not-
    403 pattern (matches test_service_requests_api.py's cross-user
    convention), not a new authorization mechanism."""
    import uuid

    from app.models.user import User
    from tests.conftest import auth_headers_for

    other_user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_user)
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "HOST"},
        headers=auth_headers_for(other_user),
    )
    assert resp.status_code == 404


async def test_get_property_call_handling_by_non_owner_is_rejected(client, db_session, test_property):
    import uuid

    from app.models.user import User
    from tests.conftest import auth_headers_for

    other_user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_user)
    await db_session.commit()

    resp = await client.get(f"/api/v1/properties/{test_property.id}", headers=auth_headers_for(other_user))
    assert resp.status_code == 404


async def test_update_property_call_handling_requires_authentication(client, test_property):
    """No Authorization header at all -- distinct from the cross-user 404
    case above, exercises the auth dependency itself rather than the
    ownership check."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "HOST"},
    )
    assert resp.status_code == 401


async def test_update_property_full_scheduled_configuration_round_trips(client, auth_headers, test_property):
    """End-to-end Phase 3 happy path: mode + start + end + timezone all set
    together, exactly as the new UI form will submit them."""
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={
            "call_handling_mode": "SCHEDULED",
            "call_handling_schedule_start": "09:00",
            "call_handling_schedule_end": "17:00",
            "timezone": "Asia/Kolkata",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_handling_mode"] == "SCHEDULED"
    assert body["call_handling_schedule_start"] == "09:00"
    assert body["call_handling_schedule_end"] == "17:00"
    assert body["timezone"] == "Asia/Kolkata"


async def test_update_property_switch_from_scheduled_back_to_mira_preserves_schedule(
    client, auth_headers, test_property
):
    """Switching mode away from SCHEDULED must not destroy the previously
    saved schedule -- a host flipping back to SCHEDULED later should see
    their old hours still there. The route never clears fields that weren't
    included in the request (exclude_unset), so this is really confirming
    that behavior end-to-end for this specific field group."""
    await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={
            "call_handling_mode": "SCHEDULED",
            "call_handling_schedule_start": "09:00",
            "call_handling_schedule_end": "17:00",
        },
        headers=auth_headers,
    )
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "MIRA"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["call_handling_mode"] == "MIRA"
    assert resp.json()["call_handling_schedule_start"] == "09:00"
    assert resp.json()["call_handling_schedule_end"] == "17:00"

    back_to_scheduled = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"call_handling_mode": "SCHEDULED"},
        headers=auth_headers,
    )
    # The cross-field validator requires both times supplied in the SAME
    # request that sets SCHEDULED (see PropertyUpdate's own validator
    # comment) -- confirms that documented, intentional strictness still
    # holds, even though the values are still sitting on the row.
    assert back_to_scheduled.status_code == 422


async def test_portfolio_gallery_returns_every_property_under_host(client, test_property, test_user, db_session):
    from app.models.property import Property

    test_property.photos = ["https://example.com/photo1.jpg"]
    second = Property(
        user_id=test_user.id, name="Second Villa", base_price=2000, max_guests=3, exophone="+918011117777"
    )
    db_session.add(second)
    await db_session.commit()

    # No-auth: the send_photos voice tool hands this URL to a guest over
    # WhatsApp, so it must be reachable with no bearer token.
    resp = await client.get(f"/api/v1/properties/portfolio/{test_user.id}/gallery")
    assert resp.status_code == 200
    body = resp.json()
    names = {p["name"] for p in body}
    assert names == {"Test Villa", "Second Villa"}
    test_villa = next(p for p in body if p["name"] == "Test Villa")
    assert test_villa["photos"] == ["https://example.com/photo1.jpg"]


async def test_portfolio_gallery_empty_for_host_with_no_properties(client, test_user):
    import uuid

    resp = await client.get(f"/api/v1/properties/portfolio/{uuid.uuid4()}/gallery")
    assert resp.status_code == 200
    assert resp.json() == []
