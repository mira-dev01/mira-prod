import uuid

from app.models.call_quality_event import CallQualityEvent
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.user import User
from app.services import lead_service
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from tests.conftest import auth_headers_for


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


async def test_quality_events_analytics_empty_for_host_with_no_events(client, auth_headers):
    resp = await client.get("/api/v1/analytics/quality-events", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["most_frequent"] == []
    assert body["over_time"] == []


async def test_quality_events_analytics_groups_by_rule_and_severity(
    client, auth_headers, test_user, test_call_session, db_session
):
    db_session.add_all(
        [
            CallQualityEvent(
                call_session_id=test_call_session.id,
                rule="style_compliance",
                severity="FAIL",
                confidence=0.9,
                turn_index=1,
                processing_time_ms=1.0,
            ),
            CallQualityEvent(
                call_session_id=test_call_session.id,
                rule="style_compliance",
                severity="FAIL",
                confidence=0.9,
                turn_index=2,
                processing_time_ms=1.0,
            ),
            CallQualityEvent(
                call_session_id=test_call_session.id,
                rule="response_shape",
                severity="WARNING",
                confidence=0.5,
                turn_index=3,
                processing_time_ms=1.0,
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get("/api/v1/analytics/quality-events", headers=auth_headers)
    assert resp.status_code == 200
    most_frequent = resp.json()["most_frequent"]
    assert {"rule": "style_compliance", "severity": "FAIL", "count": 2} in most_frequent
    assert {"rule": "response_shape", "severity": "WARNING", "count": 1} in most_frequent


async def test_quality_events_analytics_scoped_to_authenticated_host(
    client, test_user, test_call_session, db_session
):
    # Regression/tenancy check: a quality event belonging to a different
    # host's call must never appear in this host's analytics, even though
    # it's a perfectly valid row in the same table.
    other_user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    db_session.add(
        CallQualityEvent(
            call_session_id=test_call_session.id,  # belongs to test_user, not other_user
            rule="style_compliance",
            severity="FAIL",
            confidence=0.9,
            turn_index=1,
            processing_time_ms=1.0,
        )
    )
    await db_session.commit()

    own_resp = await client.get("/api/v1/analytics/quality-events", headers=auth_headers_for(test_user))
    other_resp = await client.get("/api/v1/analytics/quality-events", headers=auth_headers_for(other_user))

    assert own_resp.json()["most_frequent"] != []
    assert other_resp.json()["most_frequent"] == []


def _tagged_call_session(user_id, tags):
    return CallSession(
        user_id=user_id,
        status="completed",
        ai_summary={
            "objection_tags": tags,
            "conversation_summary": "x",
            "outcome": {"status": "x", "reason": "x"},
            "booking_snapshot": {},
        },
    )


async def test_objection_insights_empty_for_host_with_no_tagged_calls(client, auth_headers):
    resp = await client.get("/api/v1/analytics/objection-insights", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["by_tag"] == []
    assert body["baseline"] == {
        "total_calls": 0,
        "resolved_count": 0,
        "unresolved_count": 0,
        "conversion_rate": None,
    }


async def test_objection_insights_conversion_rate_and_multi_tag_handling(
    client, auth_headers, test_user, db_session
):
    # A call carrying two tags must count toward BOTH tags' numerator and
    # denominator (Implementation 2's review flagged this ambiguity
    # explicitly) -- not partitioned into one exclusive bucket.
    lost_price = _tagged_call_session(test_user.id, ["PRICE_TOO_HIGH"])
    won_price = _tagged_call_session(test_user.id, ["PRICE_TOO_HIGH"])
    lost_dates = _tagged_call_session(test_user.id, ["DATES_UNAVAILABLE"])
    won_both = _tagged_call_session(test_user.id, ["PRICE_TOO_HIGH", "DATES_UNAVAILABLE"])
    smooth = _tagged_call_session(test_user.id, ["NO_OBJECTION"])
    untagged = CallSession(user_id=test_user.id, status="completed")  # ai_summary=None

    db_session.add_all([lost_price, won_price, lost_dates, won_both, smooth, untagged])
    await db_session.commit()

    for cs in (won_price, won_both, smooth):
        lead = Lead(user_id=test_user.id, call_session_id=cs.id, status="booked")
        db_session.add(lead)
        await db_session.flush()
        cs.lead_id = lead.id
    await db_session.commit()

    resp = await client.get("/api/v1/analytics/objection-insights", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    by_tag = {row["tag"]: row for row in body["by_tag"]}
    assert by_tag["PRICE_TOO_HIGH"] == {
        "tag": "PRICE_TOO_HIGH",
        "total_calls": 3,
        "resolved_count": 2,
        "unresolved_count": 1,
        "conversion_rate": round(2 / 3, 3),
    }
    assert by_tag["DATES_UNAVAILABLE"] == {
        "tag": "DATES_UNAVAILABLE",
        "total_calls": 2,
        "resolved_count": 1,
        "unresolved_count": 1,
        "conversion_rate": 0.5,
    }
    # NO_OBJECTION is not an objection -- must not appear in by_tag.
    assert "NO_OBJECTION" not in by_tag

    # baseline covers every call with an ai_summary (5), excluding the
    # untagged call with ai_summary=None entirely.
    assert body["baseline"] == {
        "total_calls": 5,
        "resolved_count": 3,
        "unresolved_count": 2,
        "conversion_rate": 0.6,
    }


async def test_objection_insights_scoped_to_authenticated_host(client, test_user, db_session):
    other_user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    db_session.add(_tagged_call_session(test_user.id, ["PRICE_TOO_HIGH"]))
    await db_session.commit()

    own_resp = await client.get("/api/v1/analytics/objection-insights", headers=auth_headers_for(test_user))
    other_resp = await client.get("/api/v1/analytics/objection-insights", headers=auth_headers_for(other_user))

    assert own_resp.json()["by_tag"] != []
    assert other_resp.json()["by_tag"] == []
    assert other_resp.json()["baseline"]["total_calls"] == 0
