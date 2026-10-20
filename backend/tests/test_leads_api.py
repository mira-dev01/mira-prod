from tests.conftest import auth_headers_for

from app.services import lead_service, recovery_service


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


async def test_normal_lead_has_no_recovery_reason_via_api(client, auth_headers, test_user, test_call_session, db_session):
    # A normal, answered-call lead must show entry_channel="phone_call"
    # (the only real entry point that has ever existed) and no
    # recovery_reason -- recovery metadata is only ever set by a system-
    # driven recovery flow, never a default/placeholder value.
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Priya")

    resp = await client.get("/api/v1/leads", headers=auth_headers)
    lead_out = resp.json()[0]
    assert lead_out["entry_channel"] == "phone_call"
    assert lead_out["recovery_reason"] is None
    assert lead_out["lead_source"] == "voice_call"


async def test_busy_recovery_lead_exposes_recovery_reason_via_api(client, test_user, test_property, db_session):
    # End-to-end: a real busy-call-recovery lead's recovery_reason must
    # actually reach the dashboard through GET /leads, not just exist on
    # the ORM model -- this is the serializer/API half of Phase 4's goal.
    await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999991",
        dialed_number=test_property.exophone,
    )

    resp = await client.get("/api/v1/leads", headers=auth_headers_for(test_user))
    assert resp.status_code == 200
    leads = resp.json()
    assert len(leads) == 1
    assert leads[0]["recovery_reason"] == "BUSY_CALL"
    # lead_source is unaffected by recovery -- still the normal default,
    # confirming the two fields didn't collapse back into one meaning.
    assert leads[0]["lead_source"] == "voice_call"
