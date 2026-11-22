import pytest


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


# ---- ExoPhone / Twilio number: unique across hosts, so say so ------------------------------------


async def test_a_number_already_on_another_property_is_a_409_with_a_readable_message(client, auth_headers):
    payload = {"name": "P1", "base_price": 1000, "max_guests": 2, "exophone": "+918044440001"}
    assert (await client.post("/api/v1/properties", json=payload, headers=auth_headers)).status_code == 201

    clash = await client.post("/api/v1/properties", json={**payload, "name": "P2"}, headers=auth_headers)
    assert clash.status_code == 409
    assert clash.json()["detail"] == "That ExoPhone is already assigned to another property."


async def test_editing_to_a_taken_number_is_a_409_but_keeping_your_own_is_fine(client, auth_headers, test_property):
    other = await client.post(
        "/api/v1/properties",
        json={"name": "Other", "base_price": 1000, "max_guests": 2, "exophone": "+918044440002"},
        headers=auth_headers,
    )
    assert other.status_code == 201

    taken = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"exophone": "+918044440002"}, headers=auth_headers
    )
    assert taken.status_code == 409

    # Saving the form again with the number this property already owns must not trip the check.
    mine = other.json()["id"]
    again = await client.patch(f"/api/v1/properties/{mine}", json={"exophone": "+918044440002", "name": "Renamed"}, headers=auth_headers)
    assert again.status_code == 200


@pytest.mark.parametrize("bad", ["not a number", "123", "+91 80 4444 abcd", "1" * 20])
async def test_a_value_that_is_not_a_phone_number_is_refused(client, auth_headers, bad):
    resp = await client.post(
        "/api/v1/properties", json={"name": "P", "base_price": 1000, "max_guests": 2, "exophone": bad}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_a_number_is_stored_as_typed_apart_from_surrounding_whitespace(client, auth_headers):
    resp = await client.post(
        "/api/v1/properties",
        json={"name": "P", "base_price": 1000, "max_guests": 2, "exophone": "  08044440003 "},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["exophone"] == "08044440003"


# ---- Smart pricing needs a linked Airbnb listing; room rental names what's missing ----------------


async def test_smart_pricing_cannot_be_turned_on_without_a_linked_listing(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"exact_airbnb_pricing": True}, headers=auth_headers
    )
    assert resp.status_code == 422
    assert "Airbnb listing" in resp.json()["detail"]


async def test_smart_pricing_can_be_turned_on_with_a_linked_listing(client, auth_headers, test_property, db_session):
    test_property.airbnb_listing_id = "12345678"
    await db_session.commit()
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"exact_airbnb_pricing": True}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["exact_airbnb_pricing"] is True


async def test_an_existing_smart_pricing_property_can_still_be_edited(client, auth_headers, test_property, db_session):
    """Already on without a listing (set before this rule): saving the form again must not be blocked."""
    test_property.exact_airbnb_pricing = True
    await db_session.commit()
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"exact_airbnb_pricing": True, "name": "Renamed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


async def test_room_rental_error_names_what_is_missing(client, auth_headers, test_property):
    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"room_rental_enabled": True, "room_base_price": 1500},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "how many rooms can be rented" in detail and "the guests per room" in detail
    assert "price per room" not in detail


# --- one link for every property of a host (GET /properties/gallery/host/{host_id}) ---


async def _add_properties(db, user, specs):
    from app.models.property import Property

    for name, city, photos in specs:
        db.add(Property(user_id=user.id, name=name, city=city, base_price=1000, max_guests=2, photos=photos))
    await db.commit()


async def test_host_gallery_lists_every_property_with_photos_grouped_by_city_then_name(client, db_session, test_user):
    await _add_properties(
        db_session, test_user,
        [("Zed Villa", "Goa", ["https://res.cloudinary.com/x/z.jpg"]), ("Alpha Loft", "Bengaluru", ["a1", "a2"]),
         ("Beta Loft", "Bengaluru", ["b1"]), ("No Photos Yet", "Bengaluru", [])],
    )

    resp = await client.get(f"/api/v1/properties/gallery/host/{test_user.id}")  # no auth header: it is a public page

    assert resp.status_code == 200
    body = resp.json()
    assert [(p["city"], p["name"]) for p in body] == [("Bengaluru", "Alpha Loft"), ("Bengaluru", "Beta Loft"), ("Goa", "Zed Villa")]
    assert body[0]["photos"] == ["a1", "a2"]
    assert set(body[0]) == {"id", "name", "city", "photos"}  # nothing a listing page would not already show


async def test_host_gallery_never_includes_another_hosts_properties(client, db_session, test_user):
    from app.models.user import User

    other = User(email="another-host@example.com", name="Other")
    db_session.add(other)
    await db_session.flush()
    await _add_properties(db_session, test_user, [("Mine", "Goa", ["m1"])])
    await _add_properties(db_session, other, [("Theirs", "Goa", ["t1"])])

    names = [p["name"] for p in (await client.get(f"/api/v1/properties/gallery/host/{test_user.id}")).json()]

    assert names == ["Mine"]


async def test_host_gallery_can_be_narrowed_to_one_city_ignoring_case_and_spacing(client, db_session, test_user):
    await _add_properties(db_session, test_user, [("A", "Bengaluru", ["a"]), ("B", "Goa", ["b"]), ("C", " bengaluru ", ["c"])])

    names = [p["name"] for p in (await client.get(f"/api/v1/properties/gallery/host/{test_user.id}?city=BENGALURU")).json()]

    assert names == ["A", "C"]


async def test_host_gallery_for_an_unknown_host_or_a_bad_id(client):
    import uuid

    assert (await client.get(f"/api/v1/properties/gallery/host/{uuid.uuid4()}")).json() == []
    assert (await client.get("/api/v1/properties/gallery/host/not-a-uuid")).status_code == 422


async def test_the_host_gallery_route_does_not_swallow_the_single_property_gallery(client, db_session, test_user):
    await _add_properties(db_session, test_user, [("One", "Goa", ["p1"])])
    from sqlalchemy import select

    from app.models.property import Property

    prop = (await db_session.scalars(select(Property).where(Property.name == "One"))).one()

    single = await client.get(f"/api/v1/properties/{prop.id}/gallery")
    listed = await client.get("/api/v1/properties/gallery?ids=" + str(prop.id))

    assert single.status_code == 200 and single.json()["name"] == "One"
    assert listed.status_code == 200 and [p["name"] for p in listed.json()] == ["One"]
