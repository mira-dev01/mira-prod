async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_returns_current_user(client, auth_headers, test_user):
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == test_user.email


async def test_update_me_sets_lead_exophone(client, auth_headers):
    resp = await client.patch("/api/v1/auth/me", json={"lead_exophone": "+9180012340099"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["lead_exophone"] == "+9180012340099"


async def test_update_me_rejects_duplicate_lead_exophone(client, auth_headers, db_session):
    from tests.conftest import auth_headers_for
    from app.models.user import User

    other_user = User(email="other-host@example.com", name="Other Host")
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    await client.patch(
        "/api/v1/auth/me",
        json={"lead_exophone": "+9180099990000"},
        headers=auth_headers_for(other_user),
    )

    resp = await client.patch(
        "/api/v1/auth/me", json={"lead_exophone": "+9180099990000"}, headers=auth_headers
    )
    assert resp.status_code == 409


def _onboarding_payload(**overrides):
    payload = {
        "name": "Priya Sharma",
        "phone": "+919876543210",
        "business_name": "Sharma Stays",
        "business_phone": "+9180011122233",
        "airbnb_host_status": "superhost",
        "property_count_estimate": 5,
        "airbnb_url": "https://www.airbnb.co.in/rooms/12345678",
        "ical_url": "https://www.airbnb.com/calendar/ical/12345678.ics?s=abc",
    }
    payload.update(overrides)
    return payload


async def test_onboarding_requires_auth(client):
    resp = await client.post("/api/v1/auth/onboarding", json=_onboarding_payload())
    assert resp.status_code == 401


async def test_onboarding_updates_profile_when_scrape_trigger_fails(client, auth_headers, monkeypatch):
    from app.integrations import bright_data_client
    from app.integrations.bright_data_client import BrightDataError

    async def failing_trigger_scrape(urls, timeout=15.0):
        raise BrightDataError("BRIGHT_DATA_API_KEY is not configured")

    monkeypatch.setattr(bright_data_client, "trigger_scrape", failing_trigger_scrape)

    # Onboarding must still succeed even when the scrape trigger fails --
    # see onboard_host in app/api/v1/auth.py: the profile update is
    # committed before the scrape is attempted, and a BrightDataError only
    # surfaces as import_error.
    resp = await client.post("/api/v1/auth/onboarding", json=_onboarding_payload(), headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_id"] is None
    assert body["import_error"] is not None

    me_resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["business_name"] == "Sharma Stays"
    assert me["lead_exophone"] == "+9180011122233"
    assert me["airbnb_host_status"] == "superhost"
    assert me["property_count_estimate"] == 5
    assert me["timezone"] == "Asia/Kolkata"


async def test_onboarding_triggers_scrape_when_configured(client, auth_headers, monkeypatch):
    from app.integrations import bright_data_client

    async def fake_trigger_scrape(urls, timeout=15.0):
        assert urls == ["https://www.airbnb.co.in/rooms/99999999"]
        return "snap_123"

    monkeypatch.setattr(bright_data_client, "trigger_scrape", fake_trigger_scrape)

    resp = await client.post(
        "/api/v1/auth/onboarding",
        json=_onboarding_payload(
            business_phone="+9180099911122",
            airbnb_url="https://www.airbnb.co.in/rooms/99999999",
        ),
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_id"] == "snap_123"
    assert body["import_error"] is None


async def test_onboarding_rejects_duplicate_business_phone(client, auth_headers, db_session):
    from tests.conftest import auth_headers_for
    from app.models.user import User

    other_user = User(email="other-onboard@example.com", name="Other Host", lead_exophone="+9180022233344")
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    resp = await client.post(
        "/api/v1/auth/onboarding",
        json=_onboarding_payload(business_phone="+9180022233344"),
        headers=auth_headers,
    )
    assert resp.status_code == 409


async def test_onboarding_requires_airbnb_url(client, auth_headers):
    payload = _onboarding_payload()
    del payload["airbnb_url"]
    resp = await client.post("/api/v1/auth/onboarding", json=payload, headers=auth_headers)
    assert resp.status_code == 422


async def test_update_me_sets_host_phone(client, auth_headers):
    """Phase 5: User.phone is the canonical host contact number, reused
    (not duplicated) for escalation, call ownership routing, and future
    live takeover. This is the same PATCH /auth/me endpoint every other
    host self-service setting already goes through -- no new
    authorization surface was added for Phase 5."""
    resp = await client.patch("/api/v1/auth/me", json={"phone": "+919812345678"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["phone"] == "+919812345678"


async def test_update_me_phone_change_does_not_affect_unrelated_fields(client, auth_headers, test_user):
    """Setting phone must not clobber other settings -- exclude_unset means
    only the fields present in this request are touched."""
    await client.patch("/api/v1/auth/me", json={"notification_email": "ops@example.com"}, headers=auth_headers)
    resp = await client.patch("/api/v1/auth/me", json={"phone": "+919812345678"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["phone"] == "+919812345678"
    assert body["notification_email"] == "ops@example.com"


async def test_update_me_cannot_change_another_hosts_phone(client, auth_headers, db_session):
    """A host can only ever update current_user's own row -- PATCH /auth/me
    has no property_id/user_id parameter to target another host's account
    with, so there is no request shape that could update someone else's
    phone number through this endpoint."""
    from app.models.user import User

    other_user = User(
        email="other-host-phone@example.com", name="Other Host", phone="+919800000000"
    )
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    resp = await client.patch("/api/v1/auth/me", json={"phone": "+919812345678"}, headers=auth_headers)
    assert resp.status_code == 200

    await db_session.refresh(other_user)
    assert other_user.phone == "+919800000000"
