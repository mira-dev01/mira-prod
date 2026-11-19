import uuid

from sqlalchemy import select

from app.models.host_onboarding import HostOnboarding
from app.models.user import User
from tests.conftest import auth_headers_for

ONBOARDING = "/api/v1/onboarding"


async def test_new_host_has_not_started_onboarding(client, auth_headers):
    resp = await client.get(ONBOARDING, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_started"
    assert body["current_step"] == "profile"
    # Pre-selected with the registry defaults.
    assert "lead_agent" in body["selected_capabilities"]
    assert "live_airbnb_pricing" not in body["selected_capabilities"]


async def test_existing_host_with_properties_is_never_sent_through_onboarding(
    client, auth_headers, test_property, db_session, test_user
):
    resp = await client.get(ONBOARDING, headers=auth_headers)
    body = resp.json()
    assert body["status"] == "completed"
    assert body["source"] == "legacy"
    row = await db_session.scalar(select(HostOnboarding).where(HostOnboarding.user_id == test_user.id))
    assert row is not None and row.status == "completed"


async def test_existing_host_with_lead_number_only_is_legacy(client, db_session):
    user = User(email=f"h-{uuid.uuid4().hex[:6]}@example.com", name="Lead Only", lead_exophone="+918099990000")
    db_session.add(user)
    await db_session.commit()
    resp = await client.get(ONBOARDING, headers=auth_headers_for(user))
    assert resp.json()["status"] == "completed"


async def test_progress_persists_and_resumes(client, auth_headers):
    resp = await client.put(
        ONBOARDING,
        json={"current_step": "capabilities", "completed_steps": ["profile"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # "Refresh the browser": a fresh GET returns the same place.
    body = (await client.get(ONBOARDING, headers=auth_headers)).json()
    assert body["status"] == "in_progress"
    assert body["current_step"] == "capabilities"
    assert body["completed_steps"] == ["profile"]


async def test_selection_applies_capabilities(client, auth_headers, db_session, test_user):
    resp = await client.put(
        ONBOARDING,
        json={"selected_capabilities": ["lead_agent", "calendar_sync"], "current_step": "setup"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["selected_capabilities"] == ["lead_agent", "calendar_sync"]

    caps = {c["id"]: c for c in (await client.get("/api/v1/capabilities", headers=auth_headers)).json()["capabilities"]}
    assert caps["lead_agent"]["enabled"] is True
    assert caps["calendar_sync"]["enabled"] is True
    assert caps["technician_dispatch"]["enabled"] is False
    assert caps["negotiation"]["enabled"] is False
    await db_session.refresh(test_user)
    assert test_user.negotiation_allowed is False


async def test_selection_rejects_core_or_unknown_ids(client, auth_headers):
    for bad in (["busy_call_recovery"], ["nope"]):
        resp = await client.put(ONBOARDING, json={"selected_capabilities": bad}, headers=auth_headers)
        assert resp.status_code == 422


async def test_blocked_deselection_is_explained_not_applied(client, auth_headers, db_session, test_user):
    test_user.lead_exophone = "+918022223333"
    await db_session.commit()
    # Has a lead number -> would be legacy on GET, so seed an in-progress row.
    db_session.add(HostOnboarding(user_id=test_user.id, status="in_progress"))
    await db_session.commit()

    resp = await client.put(ONBOARDING, json={"selected_capabilities": []}, headers=auth_headers)
    assert resp.status_code == 200
    assert any("Portfolio Lead Agent" in note for note in resp.json()["selection_notes"])


async def test_complete_requires_profile_name(client, db_session):
    user = User(email=f"h-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(user)
    await db_session.commit()
    headers = auth_headers_for(user)

    assert (await client.post(f"{ONBOARDING}/complete", headers=headers)).status_code == 400

    await client.patch("/api/v1/auth/me", json={"name": "Asha"}, headers=headers)
    resp = await client.post(f"{ONBOARDING}/complete", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    # Idempotent.
    assert (await client.post(f"{ONBOARDING}/complete", headers=headers)).json()["status"] == "completed"
    # And further progress edits are refused.
    assert (await client.put(ONBOARDING, json={"current_step": "setup"}, headers=headers)).status_code == 409
