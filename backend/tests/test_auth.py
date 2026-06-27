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
