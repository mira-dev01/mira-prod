async def test_register_and_login(client):
    payload = {"email": "newhost@example.com", "password": "supersecret1"}
    register_resp = await client.post("/api/v1/auth/register", json=payload)
    assert register_resp.status_code == 201
    assert "access_token" in register_resp.json()

    login_resp = await client.post("/api/v1/auth/login", json=payload)
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    me_resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "newhost@example.com"


async def test_login_wrong_password(client):
    payload = {"email": "wronghost@example.com", "password": "supersecret1"}
    await client.post("/api/v1/auth/register", json=payload)

    resp = await client.post(
        "/api/v1/auth/login", json={"email": "wronghost@example.com", "password": "nope"}
    )
    assert resp.status_code == 401


async def test_duplicate_registration_rejected(client):
    payload = {"email": "dupe@example.com", "password": "supersecret1"}
    first = await client.post("/api/v1/auth/register", json=payload)
    second = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409


async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_update_me_sets_lead_exophone(client, auth_headers):
    resp = await client.patch("/api/v1/auth/me", json={"lead_exophone": "+9180012340099"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["lead_exophone"] == "+9180012340099"


async def test_update_me_rejects_duplicate_lead_exophone(client, auth_headers):
    other = await client.post(
        "/api/v1/auth/register", json={"email": "other-host@example.com", "password": "supersecret1"}
    )
    other_token = other.json()["access_token"]
    await client.patch(
        "/api/v1/auth/me",
        json={"lead_exophone": "+9180099990000"},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    resp = await client.patch(
        "/api/v1/auth/me", json={"lead_exophone": "+9180099990000"}, headers=auth_headers
    )
    assert resp.status_code == 409


def _register_host_payload(**overrides):
    payload = {
        "email": "fullhost@example.com",
        "password": "supersecret1",
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


async def test_register_host_creates_account_when_scrape_trigger_fails(client, monkeypatch):
    from app.integrations import bright_data_client
    from app.integrations.bright_data_client import BrightDataError

    async def failing_trigger_scrape(urls, timeout=15.0):
        raise BrightDataError("BRIGHT_DATA_API_KEY is not configured")

    monkeypatch.setattr(bright_data_client, "trigger_scrape", failing_trigger_scrape)

    # Registration must still succeed even when the scrape trigger fails --
    # see register_host in app/api/v1/auth.py: account creation is committed
    # before the scrape is attempted, and a BrightDataError only surfaces as
    # import_error.
    resp = await client.post("/api/v1/auth/register-host", json=_register_host_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert body["snapshot_id"] is None
    assert body["import_error"] is not None

    me_resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["business_name"] == "Sharma Stays"
    assert me["lead_exophone"] == "+9180011122233"
    assert me["airbnb_host_status"] == "superhost"
    assert me["property_count_estimate"] == 5
    assert me["timezone"] == "Asia/Kolkata"
    assert me["terms_accepted_at"] is not None


async def test_register_host_triggers_scrape_when_configured(client, monkeypatch):
    from app.integrations import bright_data_client

    async def fake_trigger_scrape(urls, timeout=15.0):
        assert urls == ["https://www.airbnb.co.in/rooms/99999999"]
        return "snap_123"

    monkeypatch.setattr(bright_data_client, "trigger_scrape", fake_trigger_scrape)

    resp = await client.post(
        "/api/v1/auth/register-host",
        json=_register_host_payload(
            email="scrapehost@example.com",
            business_phone="+9180099911122",
            airbnb_url="https://www.airbnb.co.in/rooms/99999999",
        ),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["snapshot_id"] == "snap_123"
    assert body["import_error"] is None


async def test_register_host_rejects_duplicate_email(client):
    await client.post("/api/v1/auth/register-host", json=_register_host_payload())
    resp = await client.post(
        "/api/v1/auth/register-host",
        json=_register_host_payload(business_phone="+9180022233344"),
    )
    assert resp.status_code == 409


async def test_register_host_rejects_duplicate_business_phone(client):
    await client.post("/api/v1/auth/register-host", json=_register_host_payload())
    resp = await client.post(
        "/api/v1/auth/register-host",
        json=_register_host_payload(email="second@example.com"),
    )
    assert resp.status_code == 409


async def test_register_host_requires_airbnb_url(client):
    payload = _register_host_payload(email="nourl@example.com", business_phone="+9180055566677")
    del payload["airbnb_url"]
    resp = await client.post("/api/v1/auth/register-host", json=payload)
    assert resp.status_code == 422
