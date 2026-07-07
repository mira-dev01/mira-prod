from app.services import lead_service


async def test_list_leads_scoped_to_current_user(client, auth_headers, test_user, test_call_session, db_session):
    await lead_service.upsert_lead(
        db_session, test_user.id, test_call_session.id, guest_name="Asha", lead_temperature="warm"
    )

    resp = await client.get("/api/v1/leads", headers=auth_headers)
    assert resp.status_code == 200
    leads = resp.json()
    assert len(leads) == 1
    assert leads[0]["guest_name"] == "Asha"


async def test_update_lead_temperature(client, auth_headers, test_user, test_call_session, db_session):
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Rohan")

    resp = await client.patch(
        f"/api/v1/leads/{lead.id}", json={"lead_temperature": "hot"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["lead_temperature"] == "hot"


async def test_lead_requires_auth(client, test_call_session):
    resp = await client.get("/api/v1/leads")
    assert resp.status_code == 401


async def test_new_lead_defaults_to_open_status_via_api(client, auth_headers, test_user, test_call_session, db_session):
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Meera")

    resp = await client.get("/api/v1/leads", headers=auth_headers)
    assert resp.json()[0]["status"] == "open"


async def test_update_lead_status(client, auth_headers, test_user, test_call_session, db_session):
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Kabir")

    resp = await client.patch(f"/api/v1/leads/{lead.id}", json={"status": "booked"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "booked"
