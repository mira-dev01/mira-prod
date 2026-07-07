from app.models.call_session import CallSession
from app.services import lead_service
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER


async def test_browser_test_calls_excluded_from_answer_rate(client, auth_headers, test_user, db_session):
    # Regression: browser-test calls (internal QA, not real guests) were
    # counted into total_calls/completed_calls, distorting answer_rate with
    # whatever the host happened to be testing.
    real_call = CallSession(
        exotel_call_id="real-call-1",
        user_id=test_user.id,
        caller_number="+919999999999",
        status="completed",
    )
    test_call = CallSession(
        exotel_call_id=None,
        user_id=test_user.id,
        caller_number=BROWSER_TEST_CALLER_NUMBER,
        status="in_progress",
    )
    db_session.add_all([real_call, test_call])
    await db_session.commit()

    resp = await client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_calls"] == 1
    assert body["completed_calls"] == 1
    assert body["answer_rate"] == 1.0


async def test_include_test_calls_toggle_counts_browser_test_calls(client, auth_headers, test_user, db_session):
    test_call = CallSession(
        exotel_call_id=None,
        user_id=test_user.id,
        caller_number=BROWSER_TEST_CALLER_NUMBER,
        status="completed",
    )
    db_session.add(test_call)
    await db_session.commit()

    default_resp = await client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert default_resp.json()["total_calls"] == 0

    toggled_resp = await client.get(
        "/api/v1/analytics/summary?include_test_calls=true", headers=auth_headers
    )
    body = toggled_resp.json()
    assert body["total_calls"] == 1
    assert body["completed_calls"] == 1


async def test_open_leads_counted_in_summary(client, auth_headers, test_user, test_call_session, db_session):
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Ishaan")

    resp = await client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["open_leads"] == 1


async def test_open_leads_timeseries_scoped_to_lead_not_call_session(
    client, auth_headers, test_user, test_call_session, db_session
):
    # Regression: pipeline_value's timeseries branch used to bucket by
    # CallSession.created_at while filtering Lead rows with no join between
    # them, an implicit cross join that inflated every day's sum. open_leads
    # shares that same bucket_column logic -- this exercises the fixed path.
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Tara")

    resp = await client.get(
        "/api/v1/analytics/timeseries?metric=open_leads",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    points = resp.json()["points"]
    assert sum(p["value"] for p in points) == 1
