"""app/services/admin_host_service.py against a real test DB: the one-glance
host roster and per-host detail behind /admin. Calls sit on a fixed future
day so the scope window is exact; question/escalation ages are relative to
now because the 24-hour "overdue" rule is."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.admin import issue_admin_token
from app.config import settings
from app.models.call_session import CallSession
from app.models.notification import Notification
from app.models.property import Property
from app.models.ticket import Ticket
from app.models.unanswered_question import UnansweredQuestion
from app.models.user import User
from app.services import admin_host_service as hosts
from app.services import admin_monitor_service as monitor
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

DAY = datetime(2031, 3, 10, 9, 0, tzinfo=timezone.utc)
SCOPE = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))
ADMIN = "abhayatrivedi2005@gmail.com"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _host(db, name="Host", **kw) -> User:
    user = User(
        email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name=name,
        **kw,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _property(db, user, name="Villa", number=True) -> Property:
    prop = Property(
        user_id=user.id,
        name=name,
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}" if number else None,
        base_price=4000,
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


async def _call(db, user, prop=None, *, call_type="BOOKING_LEAD", caller="+919999999999", offset=0) -> CallSession:
    call = CallSession(
        user_id=user.id,
        property_id=prop.id if prop else None,
        exotel_call_id=f"ex-{uuid.uuid4().hex[:10]}",
        caller_number=caller,
        status="completed",
        call_type=call_type,
        started_at=DAY + timedelta(minutes=offset),
        ended_at=DAY + timedelta(minutes=offset + 2),
        created_at=DAY + timedelta(minutes=offset),
    )
    db.add(call)
    await db.commit()
    return call


async def _question(db, user, text, *, age_hours, status="pending", prop=None) -> None:
    db.add(
        UnansweredQuestion(
            user_id=user.id,
            property_id=prop.id if prop else None,
            question=text,
            normalized_question=text.lower(),
            status=status,
            created_at=_now() - timedelta(hours=age_hours),
        )
    )
    await db.commit()


async def _ticket(db, user, *, kind="missed_call", urgency="medium", status="open", prop=None) -> Ticket:
    ticket = Ticket(
        user_id=user.id,
        property_id=prop.id if prop else None,
        kind=kind,
        summary=f"Call back someone ({kind})",
        urgency=urgency,
        status=status,
    )
    db.add(ticket)
    await db.commit()
    return ticket


async def _row(db, user, scope=SCOPE) -> dict:
    data = await hosts.roster(db, scope)
    return next(h for h in data["hosts"] if h["user_id"] == user.id)


async def test_a_host_with_no_calls_and_no_properties_is_still_listed(db_session):
    quiet = await _host(db_session, "Quiet")
    row = await _row(db_session, quiet)
    assert row["calls"] == 0 and row["properties"] == 0
    assert "no properties yet" in row["attention"]


async def test_a_property_without_a_number_is_flagged_and_a_number_clears_it(db_session):
    host = await _host(db_session, "Setup")
    await _property(db_session, host, number=False)
    assert "no phone number connected" in (await _row(db_session, host))["attention"]

    await _property(db_session, host, "Second", number=True)
    row = await _row(db_session, host)
    assert row["number_connected"] is True
    assert row["attention"] == []


async def test_the_portfolio_line_counts_as_a_connected_number(db_session):
    host = await _host(db_session, "Portfolio", lead_exophone=f"+9181{uuid.uuid4().int % 10**8:08d}")
    await _property(db_session, host, number=False)
    assert (await _row(db_session, host))["number_connected"] is True


async def test_call_counts_are_per_host_and_in_range_only(db_session):
    a, b = await _host(db_session, "Aaa"), await _host(db_session, "Bbb")
    pa, pb = await _property(db_session, a), await _property(db_session, b)
    await _call(db_session, a, pa)
    await _call(db_session, a, pa, call_type="MISSED_AGENT_BUSY", offset=5)
    await _call(db_session, b, pb, offset=10)
    outside = await _call(db_session, a, pa)
    outside.created_at = DAY + timedelta(days=3)
    await db_session.commit()

    row_a, row_b = await _row(db_session, a), await _row(db_session, b)
    assert (row_a["calls"], row_a["missed_calls"]) == (2, 1)
    assert (row_b["calls"], row_b["missed_calls"]) == (1, 0)
    assert row_a["last_call_at"] is not None


async def test_browser_test_calls_are_hidden_unless_asked_for(db_session):
    host = await _host(db_session, "Tester")
    prop = await _property(db_session, host)
    await _call(db_session, host, prop, caller=BROWSER_TEST_CALLER_NUMBER)
    assert (await _row(db_session, host))["calls"] == 0

    with_tests = monitor.Scope(since=SCOPE.since, until=SCOPE.until, include_test_calls=True)
    assert (await _row(db_session, host, with_tests))["calls"] == 1


async def test_a_question_asked_repeatedly_is_one_question_and_age_counts_from_the_first_ask(db_session):
    host = await _host(db_session, "Asked")
    await _property(db_session, host)
    for hours in (60, 30, 2):  # same question, three times
        await _question(db_session, host, "Is there parking?", age_hours=hours)
    await _question(db_session, host, "Do you allow pets?", age_hours=1)  # fresh
    await _question(db_session, host, "Old but answered", age_hours=200, status="answered")

    row = await _row(db_session, host)
    assert row["questions_pending"] == 2
    assert row["questions_overdue"] == 1
    assert row["oldest_question_at"] < _now() - timedelta(hours=59)
    assert any("1 question unanswered over a day" in r for r in row["attention"])


async def test_a_fresh_question_does_not_raise_attention(db_session):
    host = await _host(db_session, "Fresh")
    await _property(db_session, host)
    await _question(db_session, host, "Is there a pool?", age_hours=3)
    row = await _row(db_session, host)
    assert row["questions_pending"] == 1 and row["questions_overdue"] == 0
    assert row["attention"] == []


async def test_escalation_is_attributed_through_its_call_or_its_property(db_session):
    host = await _host(db_session, "Escalated")
    other = await _host(db_session, "Other")
    prop = await _property(db_session, host)
    await _property(db_session, other)
    call = await _call(db_session, host, prop)

    old = _now() - timedelta(hours=30)
    db_session.add(Notification(call_session_id=call.id, channel="escalation", message="via call", status="new", created_at=old))
    db_session.add(Notification(property_id=prop.id, channel="escalation", message="via property", status="read", created_at=old))
    await db_session.commit()

    row = await _row(db_session, host)
    assert row["escalations_unread"] == 1  # the read one is not waiting on anyone
    assert row["escalations_overdue"] is True
    assert "an escalation unopened for over a day" in row["attention"]
    other_row = await _row(db_session, other)
    assert other_row["escalations_unread"] == 0 and other_row["escalations_overdue"] is False


async def test_a_recent_unread_escalation_is_counted_but_not_overdue(db_session):
    host = await _host(db_session, "Recent")
    prop = await _property(db_session, host)
    db_session.add(Notification(property_id=prop.id, channel="escalation", message="new", status="new"))
    await db_session.commit()
    row = await _row(db_session, host)
    assert row["escalations_unread"] == 1 and row["escalations_overdue"] is False


async def test_tickets_split_into_open_urgent_and_takeover_and_resolved_ones_vanish(db_session):
    host = await _host(db_session, "Ticketed")
    await _property(db_session, host)
    await _ticket(db_session, host, kind="missed_call", urgency="high")
    await _ticket(db_session, host, kind="promise", urgency="medium")
    await _ticket(db_session, host, kind="missed_call", status="done")
    await _ticket(db_session, host, kind="escalation", urgency="emergency", status="dismissed")

    row = await _row(db_session, host)
    assert (row["open_tickets"], row["takeover"], row["urgent_tickets"]) == (2, 1, 1)
    assert row["attention"][0] == "1 missed call to take over"


async def test_takeover_queue_lists_open_missed_call_tickets_oldest_first_across_hosts(db_session):
    a, b = await _host(db_session, "Aaa"), await _host(db_session, "Bbb")
    first = await _ticket(db_session, a, kind="missed_call")
    first.created_at = _now() - timedelta(hours=5)
    await db_session.commit()
    await _ticket(db_session, b, kind="missed_call")
    await _ticket(db_session, b, kind="promise")  # not a takeover
    await _ticket(db_session, a, kind="missed_call", status="done")

    queue = (await hosts.roster(db_session, SCOPE))["takeover"]
    assert [q["user_id"] for q in queue] == [a.id, b.id]
    assert queue[0]["host"] == "Aaa"


async def test_hosts_needing_attention_come_first(db_session):
    calm = await _host(db_session, "Calm")
    await _property(db_session, calm)
    for i in range(3):
        await _call(db_session, calm, None, offset=i)  # busiest, but nothing waiting
    urgent = await _host(db_session, "Urgent")
    await _property(db_session, urgent)
    await _ticket(db_session, urgent, kind="missed_call")

    order = [h["user_id"] for h in (await hosts.roster(db_session, SCOPE))["hosts"]]
    assert order.index(urgent.id) < order.index(calm.id)


async def test_unfinished_onboarding_is_flagged(db_session):
    from app.models.host_onboarding import HostOnboarding

    host = await _host(db_session, "Onboarding")
    await _property(db_session, host)
    db_session.add(HostOnboarding(user_id=host.id, status="in_progress"))
    await db_session.commit()
    assert "setup unfinished" in (await _row(db_session, host))["attention"]


async def test_the_busy_mode_shown_is_the_hosts_own_setting(db_session):
    host = await _host(db_session, "Busy", agent_config={"version": 1, "busy_calls": {"mode": "host_follows_up"}})
    assert (await _row(db_session, host))["busy_mode"] == "host_follows_up"
    default = await _host(db_session, "Default")
    assert (await _row(db_session, default))["busy_mode"] == "callback_invite"


async def test_cost_is_a_number_for_every_host(db_session):
    host = await _host(db_session, "Costly")
    assert (await _row(db_session, host))["cost_inr"] == 0


async def test_detail_is_none_for_an_unknown_host(db_session):
    assert await hosts.detail(db_session, uuid.uuid4(), SCOPE) is None


async def test_detail_gathers_the_hosts_own_properties_questions_tickets_and_calls(db_session):
    host, other = await _host(db_session, "Mine"), await _host(db_session, "Theirs")
    prop = await _property(db_session, host, "Beach House")
    bare = await _property(db_session, host, "Bare Room", number=False)
    other_prop = await _property(db_session, other, "Not Mine")
    await _call(db_session, host, prop)
    await _call(db_session, other, other_prop)
    await _question(db_session, host, "Is there parking?", age_hours=40, prop=prop)
    await _question(db_session, other, "Someone else's question", age_hours=40, prop=other_prop)
    await _ticket(db_session, host, kind="promise")
    await _ticket(db_session, other, kind="promise")
    db_session.add(Notification(property_id=prop.id, channel="escalation", message="mine", status="new"))
    db_session.add(Notification(property_id=other_prop.id, channel="escalation", message="theirs", status="new"))
    await db_session.commit()

    data = await hosts.detail(db_session, host.id, SCOPE)

    assert data["summary"]["user_id"] == host.id
    by_name = {p["name"]: p for p in data["properties"]}
    assert set(by_name) == {"Beach House", "Bare Room"}
    assert by_name["Beach House"]["calls"] == 1 and by_name["Beach House"]["questions_pending"] == 1
    assert by_name["Bare Room"]["number_connected"] is False
    assert len(data["recent_calls"]) == 1
    assert [q["question"] for q in data["questions"]] == ["Is there parking?"]
    assert data["questions"][0]["overdue"] is True and data["questions"][0]["property_name"] == "Beach House"
    assert len(data["tickets"]) == 1
    assert [e["message"] for e in data["escalations"]] == ["mine"]
    assert data["agent"]["busy_calls"]["mode"] == "callback_invite"
    assert isinstance(data["capabilities"], list)
    assert bare.id  # created, listed above


# ── HTTP wiring ──────────────────────────────────────────────────────────


@pytest.fixture
def admin_headers(monkeypatch):
    monkeypatch.setattr(settings, "admin_jwt_secret", "test-admin-secret-0123456789abcdef")
    monkeypatch.setattr(settings, "admin_emails", ADMIN)
    token, _ = issue_admin_token(ADMIN)
    return {"Authorization": f"Bearer {token}"}


RANGE = "start_date=2031-03-10&end_date=2031-03-10"


async def test_roster_and_detail_endpoints_answer_for_an_admin(client, admin_headers, db_session):
    host = await _host(db_session, "Http")
    await _property(db_session, host)
    await _ticket(db_session, host, kind="missed_call")

    roster = await client.get(f"/api/v1/admin/host-roster?{RANGE}", headers=admin_headers)
    assert roster.status_code == 200, roster.text
    body = roster.json()
    assert {"hosts", "takeover"} <= set(body)
    assert str(host.id) in {h["user_id"] for h in body["hosts"]}
    assert len(body["takeover"]) == 1

    detail = await client.get(f"/api/v1/admin/hosts/{host.id}?{RANGE}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert {"summary", "profile", "agent", "capabilities", "properties", "recent_calls", "tickets", "questions", "escalations"} <= set(detail.json())


async def test_unknown_host_is_404_and_no_token_is_refused(client, admin_headers):
    missing = await client.get(f"/api/v1/admin/hosts/{uuid.uuid4()}", headers=admin_headers)
    assert missing.status_code == 404
    for path in ("/api/v1/admin/host-roster", f"/api/v1/admin/hosts/{uuid.uuid4()}"):
        assert (await client.get(path)).status_code in (401, 403, 404)


# The TypeScript types in frontend/src/lib/admin-api.ts (HostRosterRow,
# TakeoverItem, HostDetailData) are written by hand; these key sets are what
# they must list, so a renamed field fails here instead of rendering blank.
ROSTER_ROW_KEYS = {
    "user_id", "name", "email", "joined_at", "status", "onboarding", "properties", "number_connected", "busy_mode",
    "calls", "missed_calls", "last_call_at", "escalations", "escalations_unread", "escalations_overdue",
    "open_tickets", "urgent_tickets", "takeover", "questions_pending", "questions_overdue", "oldest_question_at",
    "cost_inr", "attention",
}
TAKEOVER_KEYS = {"id", "user_id", "host", "property_name", "summary", "detail", "urgency", "created_at"}
DETAIL_KEYS = {
    "summary", "profile", "agent", "capabilities", "properties", "recent_calls", "tickets", "questions", "escalations",
}
PROFILE_KEYS = {"phone", "tier", "timezone", "lead_number", "notification_email"}
PROPERTY_KEYS = {"id", "name", "city", "number_connected", "payment_override", "calls", "questions_pending"}
CALL_KEYS = {"id", "created_at", "caller_number", "call_type", "status", "urgency", "duration_s"}
TICKET_KEYS = {"id", "kind", "summary", "detail", "urgency", "created_at", "property_name", "guest_name", "guest_phone"}
QUESTION_KEYS = {"question", "asked", "first_asked_at", "property_name", "overdue"}
ESCALATION_KEYS = {"message", "urgency", "status", "created_at"}
CAPABILITY_KEYS = {"id", "name", "enabled", "state"}


async def test_payload_keys_match_what_the_frontend_types_declare(client, admin_headers, db_session):
    host = await _host(db_session, "Contract")
    prop = await _property(db_session, host)
    await _call(db_session, host, prop)
    await _question(db_session, host, "Is there parking?", age_hours=30, prop=prop)
    await _ticket(db_session, host, kind="missed_call")
    db_session.add(Notification(property_id=prop.id, channel="escalation", message="x", status="new"))
    await db_session.commit()

    roster = (await client.get(f"/api/v1/admin/host-roster?{RANGE}", headers=admin_headers)).json()
    assert set(roster["hosts"][0]) == ROSTER_ROW_KEYS
    assert set(roster["takeover"][0]) == TAKEOVER_KEYS

    detail = (await client.get(f"/api/v1/admin/hosts/{host.id}?{RANGE}", headers=admin_headers)).json()
    assert set(detail) == DETAIL_KEYS
    assert set(detail["summary"]) == ROSTER_ROW_KEYS
    assert set(detail["profile"]) == PROFILE_KEYS
    assert set(detail["properties"][0]) == PROPERTY_KEYS
    assert set(detail["recent_calls"][0]) == CALL_KEYS
    assert TICKET_KEYS <= set(detail["tickets"][0])
    assert set(detail["questions"][0]) == QUESTION_KEYS
    assert set(detail["escalations"][0]) == ESCALATION_KEYS
    assert all(set(c) == CAPABILITY_KEYS for c in detail["capabilities"])
