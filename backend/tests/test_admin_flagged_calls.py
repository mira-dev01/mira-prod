"""The founder console's "Flagged calls" list: which calls went wrong this week, why, and a way to read them."""

import uuid
from datetime import datetime, timezone

import pytest

from app.models.call_quality_event import CALL_SCORECARD_RULE, CallQualityEvent
from app.models.call_session import CallSession
from app.services import admin_monitor_service as monitor
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from tests.test_admin_api import admin_headers  # noqa: F401  (fixture)

DAY = datetime(2031, 3, 10, 12, 0, tzinfo=timezone.utc)
RANGE = "start_date=2031-03-10&end_date=2031-03-10"


async def _call(db, user, *, flags, caller="+919999999999", transcript="assistant: hello\nuser: hi", recording=None, scored=True):
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}", user_id=user.id, caller_number=caller, status="completed",
        transcript=transcript, recording_url=recording, created_at=DAY,
        started_at=DAY, ended_at=datetime(2031, 3, 10, 12, 1, 30, tzinfo=timezone.utc),
    )
    db.add(session)
    await db.flush()
    if scored:
        db.add(
            CallQualityEvent(
                call_session_id=session.id, rule=CALL_SCORECARD_RULE, severity="WARNING" if flags else "INFO",
                confidence=1.0, turn_index=3, processing_time_ms=0.0,
                metadata_json={"scorecard": {"flagged": bool(flags), "flags": flags, "ended_by": "guest_hung_up", "outcome": {}}},
            )
        )
    await db.commit()
    return session


async def test_only_flagged_calls_are_listed_with_their_reasons(db_session, test_user):
    bad = await _call(db_session, test_user, flags=[{"code": "repeated_question", "detail": "asked 4 times: Which area?"}], recording="https://rec/x.mp3")
    await _call(db_session, test_user, flags=[])
    await _call(db_session, test_user, flags=[], scored=False)  # a call from before scorecards

    scope = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))
    data = await monitor.flagged_calls(db_session, scope)

    assert data["coverage"] == {"calls": 3, "scored_calls": 2, "flagged_calls": 1}
    assert [c["id"] for c in data["calls"]] == [str(bad.id)]
    assert data["calls"][0]["flags"][0]["code"] == "repeated_question"
    assert data["calls"][0]["flags"][0]["label"] == "Mira repeated the same question"
    assert data["calls"][0]["has_recording"] is True
    assert data["calls"][0]["duration_s"] == 90.0
    assert data["calls"][0]["host"]  # named, not a bare id
    assert data["reasons"] == [{"code": "repeated_question", "label": "Mira repeated the same question", "count": 1}]


async def test_browser_test_calls_are_left_out_unless_asked_for(db_session, test_user):
    await _call(db_session, test_user, flags=[{"code": "tool_error", "detail": "x"}], caller=BROWSER_TEST_CALLER_NUMBER)
    since, until = datetime(2031, 3, 10, tzinfo=timezone.utc), datetime(2031, 3, 11, tzinfo=timezone.utc)

    assert (await monitor.flagged_calls(db_session, monitor.Scope(since=since, until=until)))["calls"] == []
    included = await monitor.flagged_calls(db_session, monitor.Scope(since=since, until=until, include_test_calls=True))
    assert len(included["calls"]) == 1


async def test_reasons_are_counted_most_common_first(db_session, test_user):
    await _call(db_session, test_user, flags=[{"code": "tool_error", "detail": "a"}, {"code": "slow_response", "detail": "b"}])
    await _call(db_session, test_user, flags=[{"code": "slow_response", "detail": "c"}])
    scope = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))

    reasons = (await monitor.flagged_calls(db_session, scope))["reasons"]

    assert [(r["code"], r["count"]) for r in reasons] == [("slow_response", 2), ("tool_error", 1)]


async def test_the_endpoint_answers_and_the_detail_carries_the_transcript(client, admin_headers, db_session, test_user):  # noqa: F811
    call = await _call(db_session, test_user, flags=[{"code": "tool_error", "detail": "get_pricing x1"}], transcript="assistant: hi\nuser: [inaudible]", recording="https://rec/y.mp3")

    listed = await client.get(f"/api/v1/admin/flagged-calls?{RANGE}", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert {"coverage", "reasons", "calls"} <= set(listed.json())
    assert listed.json()["calls"][0]["id"] == str(call.id)

    detail = await client.get(f"/api/v1/admin/flagged-calls/{call.id}", headers=admin_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["transcript"] == "assistant: hi\nuser: [inaudible]"
    assert body["recording_url"] == "https://rec/y.mp3"
    assert body["scorecard"]["flags"][0]["code"] == "tool_error"


async def test_an_unknown_call_is_a_404(client, admin_headers):  # noqa: F811
    resp = await client.get(f"/api/v1/admin/flagged-calls/{uuid.uuid4()}", headers=admin_headers)
    assert resp.status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/admin/flagged-calls", f"/api/v1/admin/flagged-calls/{uuid.uuid4()}"])
async def test_the_routes_need_an_admin_session(client, admin_headers, path):  # noqa: F811  (admin auth is configured; no token sent)
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers={"Authorization": "Bearer 00000000-0000-0000-0000-000000000000"})).status_code == 401


async def test_weekly_numbers_per_host_are_shares_of_scored_calls(db_session, test_user):
    async def card_call(flags, outcome, ended_by):
        call = await _call(db_session, test_user, flags=flags)
        event = (await db_session.scalars(
            __import__("sqlalchemy").select(CallQualityEvent).where(CallQualityEvent.call_session_id == call.id)
        )).one()
        event.metadata_json = {"scorecard": {"flagged": bool(flags), "flags": flags, "ended_by": ended_by, "outcome": outcome}}
        await db_session.commit()

    await card_call([{"code": "tool_error", "detail": "x"}], {"lead": False, "booking": False, "escalation": False}, "guest_hung_up")
    await card_call([], {"lead": True}, "mira_ended")
    await card_call([], {"escalation": True}, "mira_ended")
    await card_call([], {"lead": False, "booking": False, "escalation": False}, "guest_hung_up")
    scope = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))

    numbers = (await monitor.flagged_calls(db_session, scope))["weekly_numbers"]

    assert len(numbers) == 1
    assert numbers[0]["scored_calls"] == 4
    assert numbers[0]["flagged_share"] == 0.25
    assert numbers[0]["outcome_share"] == 0.5  # a lead, and an escalation
    assert numbers[0]["guest_hung_up_share"] == 0.5


async def test_the_unflagged_sample_is_ten_distinct_unflagged_calls(db_session, test_user):
    bad = await _call(db_session, test_user, flags=[{"code": "tool_error", "detail": "x"}])
    clean_ids = {str((await _call(db_session, test_user, flags=[])).id) for _ in range(14)}
    scope = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))

    sample = (await monitor.flagged_calls(db_session, scope))["unflagged_sample"]

    ids = [c["id"] for c in sample]
    assert len(ids) == 10 and len(set(ids)) == 10
    assert set(ids) <= clean_ids and str(bad.id) not in ids
    assert all(c["flags"] == [] for c in sample)


async def test_a_small_week_gives_a_smaller_sample_not_an_error(db_session, test_user):
    await _call(db_session, test_user, flags=[])
    await _call(db_session, test_user, flags=[])
    scope = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))

    assert len((await monitor.flagged_calls(db_session, scope))["unflagged_sample"]) == 2
