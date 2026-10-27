import uuid

import pytest
from sqlalchemy import select

from app.integrations import bright_data_client, cloudinary_client
from app.models.host_onboarding import HostOnboarding
from app.models.property import Property
from app.models.user import User
from app.services import onboarding_service
from tests.conftest import auth_headers_for
from tests.test_airbnb_import import _bright_data_record

ONBOARDING = "/api/v1/onboarding"


@pytest.fixture(autouse=True)
def _no_background_pollers(monkeypatch):
    """Tests drive the import deterministically via process_ready_snapshot
    instead of the real background poll loop; record spawn calls instead."""
    spawned: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(onboarding_service, "_spawn_poller", lambda uid, sid: spawned.append((uid, sid)))
    return spawned


@pytest.fixture
def fake_bright_data(monkeypatch):
    calls = {"trigger": 0}

    async def fake_trigger(urls, timeout=15.0):
        calls["trigger"] += 1
        return f"snap_{calls['trigger']}"

    async def fake_status(snapshot_id, timeout=15.0):
        return "ready"

    async def fake_data(snapshot_id, timeout=30.0):
        return [_bright_data_record(property_id="55555555")]

    async def fake_upload(urls, folder, max_images=10):
        return []

    monkeypatch.setattr(bright_data_client, "trigger_scrape", fake_trigger)
    monkeypatch.setattr(bright_data_client, "get_snapshot_status", fake_status)
    monkeypatch.setattr(bright_data_client, "get_snapshot_data", fake_data)
    monkeypatch.setattr(cloudinary_client, "upload_images_from_urls", fake_upload)
    return calls


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


async def test_first_property_import_is_server_side_and_idempotent(
    client, auth_headers, db_session, test_user, fake_bright_data, _no_background_pollers
):
    payload = {
        "airbnb_url": "https://www.airbnb.co.in/rooms/55555555?check_in=2026-11-01",
        "ical_url": "https://www.airbnb.com/calendar/ical/55555555.ics",
        "live_pricing": True,
    }
    resp = await client.post(f"{ONBOARDING}/first-property", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    record = resp.json()["first_property"]
    assert record["status"] == "importing"
    assert record["airbnb_url"] == "https://www.airbnb.com/rooms/55555555"
    assert _no_background_pollers == [(test_user.id, "snap_1")]

    # Double submit / refresh-and-resubmit: no second scrape.
    resp = await client.post(f"{ONBOARDING}/first-property", json=payload, headers=auth_headers)
    assert resp.json()["first_property"]["status"] == "importing"
    assert fake_bright_data["trigger"] == 1

    # The server finishes the import with no browser involved.
    await onboarding_service.process_ready_snapshot(test_user.id, "snap_1")

    body = (await client.get(ONBOARDING, headers=auth_headers)).json()
    assert body["first_property"]["status"] == "completed"
    property_id = uuid.UUID(body["first_property"]["property_id"])

    properties = (await db_session.scalars(select(Property).where(Property.user_id == test_user.id))).all()
    assert len(properties) == 1
    property_ = properties[0]
    assert property_.id == property_id
    await db_session.refresh(property_)
    assert property_.ical_url == payload["ical_url"]
    assert property_.exact_airbnb_pricing is True

    # Processing the same snapshot again (e.g. a second worker) never
    # duplicates the property.
    await onboarding_service.process_ready_snapshot(test_user.id, "snap_1")
    count = len((await db_session.scalars(select(Property).where(Property.user_id == test_user.id))).all())
    assert count == 1

    # Re-submitting after completion still doesn't re-scrape.
    await client.post(f"{ONBOARDING}/first-property", json=payload, headers=auth_headers)
    assert fake_bright_data["trigger"] == 1


async def test_get_resumes_pending_import_after_restart(
    client, auth_headers, test_user, fake_bright_data, _no_background_pollers
):
    await client.post(
        f"{ONBOARDING}/first-property",
        json={"airbnb_url": "https://www.airbnb.com/rooms/55555555"},
        headers=auth_headers,
    )
    _no_background_pollers.clear()
    # No poller registered in this "process" any more -> GET re-spawns one.
    onboarding_service._pollers.clear()
    await client.get(ONBOARDING, headers=auth_headers)
    assert _no_background_pollers == [(test_user.id, "snap_1")]


async def test_first_property_rejects_non_airbnb_url(client, auth_headers, fake_bright_data):
    resp = await client.post(
        f"{ONBOARDING}/first-property", json={"airbnb_url": "https://example.com/house"}, headers=auth_headers
    )
    assert resp.status_code == 422
    assert fake_bright_data["trigger"] == 0


async def test_first_property_scrape_failure_is_recorded_not_raised(client, auth_headers, monkeypatch):
    from app.integrations.bright_data_client import BrightDataError

    async def failing_trigger(urls, timeout=15.0):
        raise BrightDataError("BRIGHT_DATA_API_KEY is not configured")

    monkeypatch.setattr(bright_data_client, "trigger_scrape", failing_trigger)
    resp = await client.post(
        f"{ONBOARDING}/first-property",
        json={"airbnb_url": "https://www.airbnb.com/rooms/1"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    record = resp.json()["first_property"]
    assert record["status"] == "failed"
    assert "BRIGHT_DATA_API_KEY" in record["error"]


async def test_import_result_never_lands_on_another_host(
    client, auth_headers, db_session, test_user, fake_bright_data
):
    other = User(email=f"o-{uuid.uuid4().hex[:6]}@example.com", name="Other")
    db_session.add(other)
    await db_session.commit()

    await client.post(
        f"{ONBOARDING}/first-property",
        json={"airbnb_url": "https://www.airbnb.com/rooms/55555555"},
        headers=auth_headers,
    )
    # Another host can't attach to or finish this host's snapshot.
    await onboarding_service.process_ready_snapshot(other.id, "snap_1")
    assert (await db_session.scalars(select(Property).where(Property.user_id == other.id))).all() == []


# ── The real background poll loop (not just process_ready_snapshot) ──


async def _start_import(client, auth_headers):
    resp = await client.post(
        f"{ONBOARDING}/first-property",
        json={"airbnb_url": "https://www.airbnb.com/rooms/55555555"},
        headers=auth_headers,
    )
    assert resp.json()["first_property"]["status"] == "importing"


async def test_poll_loop_waits_while_running_then_imports(
    client, auth_headers, db_session, test_user, fake_bright_data, monkeypatch
):
    await _start_import(client, auth_headers)
    statuses = iter(["running", "running", "ready"])

    async def fake_status(snapshot_id, timeout=15.0):
        return next(statuses)

    monkeypatch.setattr(bright_data_client, "get_snapshot_status", fake_status)
    monkeypatch.setattr(onboarding_service, "IMPORT_POLL_INTERVAL_SECONDS", 0)

    await onboarding_service._poll_import(test_user.id, "snap_1")

    body = (await client.get(ONBOARDING, headers=auth_headers)).json()
    assert body["first_property"]["status"] == "completed"
    assert len((await db_session.scalars(select(Property).where(Property.user_id == test_user.id))).all()) == 1


async def test_poll_loop_records_failed_scrape(client, auth_headers, test_user, fake_bright_data, monkeypatch):
    await _start_import(client, auth_headers)

    async def fake_status(snapshot_id, timeout=15.0):
        return "failed"

    monkeypatch.setattr(bright_data_client, "get_snapshot_status", fake_status)
    await onboarding_service._poll_import(test_user.id, "snap_1")

    record = (await client.get(ONBOARDING, headers=auth_headers)).json()["first_property"]
    assert record["status"] == "failed"
    assert record["error"]


async def test_poll_loop_gives_up_after_max_age(client, auth_headers, test_user, fake_bright_data, monkeypatch):
    from datetime import timedelta

    await _start_import(client, auth_headers)

    async def fake_status(snapshot_id, timeout=15.0):
        return "running"

    monkeypatch.setattr(bright_data_client, "get_snapshot_status", fake_status)
    monkeypatch.setattr(onboarding_service, "IMPORT_MAX_AGE", timedelta(seconds=-1))
    await onboarding_service._poll_import(test_user.id, "snap_1")

    record = (await client.get(ONBOARDING, headers=auth_headers)).json()["first_property"]
    assert record["status"] == "failed"
    assert "too long" in record["error"]


async def test_poll_loop_crash_is_recorded_not_raised(client, auth_headers, test_user, fake_bright_data, monkeypatch):
    await _start_import(client, auth_headers)

    async def exploding_data(snapshot_id, timeout=30.0):
        raise RuntimeError("boom")

    monkeypatch.setattr(bright_data_client, "get_snapshot_data", exploding_data)
    await onboarding_service._poll_import(test_user.id, "snap_1")  # must not raise

    record = (await client.get(ONBOARDING, headers=auth_headers)).json()["first_property"]
    assert record["status"] == "failed"


async def test_retry_with_new_url_supersedes_old_snapshot(
    client, auth_headers, db_session, test_user, fake_bright_data
):
    await _start_import(client, auth_headers)
    resp = await client.post(
        f"{ONBOARDING}/first-property",
        json={"airbnb_url": "https://www.airbnb.com/rooms/77777777"},
        headers=auth_headers,
    )
    assert resp.json()["first_property"]["airbnb_url"].endswith("/77777777")
    assert fake_bright_data["trigger"] == 2

    # The stale first snapshot finishing late must not overwrite the retry.
    await onboarding_service.process_ready_snapshot(test_user.id, "snap_1")
    record = (await client.get(ONBOARDING, headers=auth_headers)).json()["first_property"]
    assert record["status"] == "importing"
    assert (await db_session.scalars(select(Property).where(Property.user_id == test_user.id))).all() == []
