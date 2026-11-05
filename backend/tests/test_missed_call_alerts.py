"""Calls that should have reached Mira and didn't: the host and admins hear at once."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pipecat.frames.frames import BotStartedSpeakingFrame, UserStoppedSpeakingFrame
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.call_session import CallSession
from app.models.notification import Notification
from app.models.ticket import Ticket
from app.models.user import User
from app.services import call_service, missed_call_service, tool_handlers
from app.services.missed_call_service import ALERT_WINDOW, Throttle
from app.voice.call_metrics import CallMetricsCollector


@pytest.fixture(autouse=True)
def _alert_channels(monkeypatch):
    """Records what would be sent; fresh throttles per test; admin emails on."""
    sent = {"whatsapp": [], "email": []}

    async def whatsapp(phone, **kwargs):
        sent["whatsapp"].append((phone, kwargs))

    async def email(to, subject, body, html_body):
        sent["email"].append((to, subject, body))

    monkeypatch.setattr(tool_handlers, "_send_escalation_whatsapp", whatsapp)
    monkeypatch.setattr(tool_handlers, "_send_escalation_email", email)
    monkeypatch.setattr(settings, "twilio_enabled", True)
    monkeypatch.setattr(settings, "health_alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_emails", "admin1@example.com,admin2@example.com")
    monkeypatch.setattr(missed_call_service, "_host_throttle", Throttle(ALERT_WINDOW))
    monkeypatch.setattr(missed_call_service, "_admin_throttle", Throttle(ALERT_WINDOW))
    return sent


@pytest.fixture
def sent(_alert_channels):
    return _alert_channels


async def _session(user_id, property_id, caller="+919876543210", **kw):
    async with AsyncSessionLocal() as db:
        row = CallSession(
            exotel_call_id=f"call-{uuid.uuid4().hex[:8]}", user_id=user_id, property_id=property_id,
            caller_number=caller, status=kw.pop("status", "failed"), **kw,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _tickets(user_id):
    async with AsyncSessionLocal() as db:
        return list((await db.scalars(select(Ticket).where(Ticket.user_id == user_id, Ticket.kind == "missed_call"))).all())


async def _set_host_phone(user_id, phone="+919811111111"):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        user.phone = phone
        await db.commit()


# ---- the throttle ------------------------------------------------------


def test_throttle_sends_the_first_holds_the_rest_and_counts_them():
    now = [datetime(2026, 11, 5, tzinfo=timezone.utc)]
    throttle = Throttle(timedelta(minutes=5), clock=lambda: now[0])
    assert throttle.take("host") == (True, 0)
    assert throttle.take("host") == (False, 0)
    now[0] += timedelta(minutes=2)
    assert throttle.take("host") == (False, 0)
    assert throttle.take("other-host") == (True, 0)  # keys are independent
    now[0] += timedelta(minutes=4)  # 6 minutes since the first
    assert throttle.take("host") == (True, 2)  # and it says how many it stands for
    assert throttle.take("host") == (False, 0)


# ---- the alert ---------------------------------------------------------


async def test_the_host_gets_a_ticket_an_alert_a_whatsapp_and_an_email(sent, test_user, test_property):
    await _set_host_phone(test_user.id)
    session = await _session(test_user.id, test_property.id)

    await missed_call_service.alert_failed_call(session.id, "pipeline_crash")

    (ticket,) = await _tickets(test_user.id)
    assert ticket.urgency == "high" and ticket.status == "open" and ticket.call_session_id == session.id
    assert "Call back +919876543210" in ticket.summary and "voice system" in ticket.detail
    async with AsyncSessionLocal() as db:
        alert = await db.get(Notification, ticket.notification_id)
    assert alert.urgency == "high" and "didn't connect" in alert.message

    (wa_phone, wa), = sent["whatsapp"]
    assert wa_phone == "+919811111111" and wa["guest_phone"] == "+919876543210" and wa["urgency"] == "high"
    host_emails = [e for e in sent["email"] if e[0] == test_user.email]
    assert len(host_emails) == 1 and "didn't connect" in host_emails[0][1]


async def test_the_admins_are_emailed_too(sent, test_user, test_property):
    session = await _session(test_user.id, test_property.id)
    await missed_call_service.alert_failed_call(session.id, "not_connected", "Exotel status: failed")
    admin_mail = [e for e in sent["email"] if e[0] in ("admin1@example.com", "admin2@example.com")]
    assert {e[0] for e in admin_mail} == {"admin1@example.com", "admin2@example.com"}
    assert "Exotel status: failed" in admin_mail[0][2] and test_user.email in admin_mail[0][2]


async def test_admins_are_not_emailed_where_health_alerts_are_off(sent, test_user, test_property, monkeypatch):
    monkeypatch.setattr(settings, "health_alerts_enabled", False)
    await missed_call_service.alert_failed_call((await _session(test_user.id, test_property.id)).id, "pipeline_crash")
    assert [e for e in sent["email"] if e[0].startswith("admin")] == []
    assert len(await _tickets(test_user.id)) == 1  # the host is still looked after


async def test_the_same_call_is_raised_once(sent, test_user, test_property):
    session = await _session(test_user.id, test_property.id)
    await missed_call_service.alert_failed_call(session.id, "pipeline_crash")
    await missed_call_service.alert_failed_call(session.id, "never_finished")  # e.g. the sweep finds it later
    assert len(await _tickets(test_user.id)) == 1
    assert len([e for e in sent["email"] if e[0] == test_user.email]) == 1


async def test_an_outage_is_one_message_and_a_rollup_but_every_call_keeps_its_ticket(
    sent, test_user, test_property, monkeypatch
):
    await _set_host_phone(test_user.id)
    clock = [datetime(2026, 11, 5, tzinfo=timezone.utc)]
    monkeypatch.setattr(missed_call_service, "_host_throttle", Throttle(ALERT_WINDOW, clock=lambda: clock[0]))
    monkeypatch.setattr(missed_call_service, "_admin_throttle", Throttle(ALERT_WINDOW, clock=lambda: clock[0]))

    for caller in ("+911000000001", "+911000000002", "+911000000003"):
        await missed_call_service.alert_failed_call((await _session(test_user.id, test_property.id, caller)).id, "pipeline_crash")
    assert len(await _tickets(test_user.id)) == 3  # nobody is lost
    assert len(sent["whatsapp"]) == 1 and len([e for e in sent["email"] if e[0] == test_user.email]) == 1
    assert len([e for e in sent["email"] if e[0] == "admin1@example.com"]) == 1

    clock[0] += timedelta(minutes=6)
    await missed_call_service.alert_failed_call((await _session(test_user.id, test_property.id, "+911000000004")).id, "pipeline_crash")
    assert len(sent["whatsapp"]) == 2 and "2 other call(s)" in sent["whatsapp"][1][1]["call_summary"]
    assert "2 other call(s)" in [e for e in sent["email"] if e[0] == "admin1@example.com"][1][2]


async def test_a_host_who_turned_escalation_email_off_gets_no_email(sent, test_user, test_property):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, test_user.id)
        user.notification_preferences = {"escalation_email": False}
        await db.commit()
    await missed_call_service.alert_failed_call((await _session(test_user.id, test_property.id)).id, "pipeline_crash")
    assert [e for e in sent["email"] if e[0] == test_user.email] == []
    assert len(await _tickets(test_user.id)) == 1


async def test_a_browser_test_call_is_nobodys_missed_guest(sent, test_user, test_property):
    session = await _session(test_user.id, test_property.id, caller=call_service.BROWSER_TEST_CALLER_NUMBER)
    await missed_call_service.alert_failed_call(session.id, "pipeline_crash")
    assert await _tickets(test_user.id) == [] and sent["email"] == [] and sent["whatsapp"] == []


async def test_a_call_with_no_known_host_still_reaches_the_admins(sent):
    session = await _session(None, None)
    await missed_call_service.alert_failed_call(session.id, "not_connected")
    assert {e[0] for e in sent["email"]} == {"admin1@example.com", "admin2@example.com"}
    assert "an unknown host" in sent["email"][0][1]


async def test_the_alert_never_raises_even_for_a_missing_call(sent):
    await missed_call_service.alert_failed_call(uuid.uuid4(), "pipeline_crash")  # no such session


# ---- the four ways in --------------------------------------------------


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0.02)


async def test_exotel_failed_and_no_answer_calls_raise_the_alarm(sent, test_user, test_property):
    for status in ("failed", "no-answer"):
        caller = f"+9198{abs(hash(status)) % 10**8:08d}"
        async with AsyncSessionLocal() as db:
            session = await call_service.attach_exotel_call(
                db, exotel_call_id=f"ex-{uuid.uuid4().hex[:8]}", caller_number=caller,
                dialed_number=test_property.exophone, status=status, recording_url=None,
            )
        assert session.call_type == "MISSED_SYSTEM_FAILURE" and session.ended_at is not None
        await _settle()
    assert len(await _tickets(test_user.id)) == 2


async def test_exotel_busy_completed_and_already_answered_calls_do_not(sent, test_user, test_property):
    async with AsyncSessionLocal() as db:
        for status in ("busy", "canceled", "completed"):
            await call_service.attach_exotel_call(
                db, exotel_call_id=f"ex-{uuid.uuid4().hex[:8]}", caller_number="+919800000001",
                dialed_number=test_property.exophone, status=status, recording_url=None,
            )
        # A call that DID reach Mira (its session already exists) is not "never connected".
        answered = await _session(test_user.id, test_property.id, status="in_progress")
        await call_service.attach_exotel_call(
            db, exotel_call_id=answered.exotel_call_id, caller_number=answered.caller_number,
            dialed_number=test_property.exophone, status="failed", recording_url=None,
        )
    await _settle()
    assert await _tickets(test_user.id) == []


async def test_a_failed_call_to_the_portfolio_number_still_finds_its_host(sent, test_user):
    async with AsyncSessionLocal() as db:
        host = await db.get(User, test_user.id)
        host.lead_exophone = f"+9180{uuid.uuid4().int % 10**8:08d}"
        await db.commit()
        number = host.lead_exophone
    async with AsyncSessionLocal() as db:
        session = await call_service.attach_exotel_call(
            db, exotel_call_id=f"ex-{uuid.uuid4().hex[:8]}", caller_number="+919800000002",
            dialed_number=number, status="failed", recording_url=None,
        )
    assert session.user_id == test_user.id
    await _settle()
    assert len(await _tickets(test_user.id)) == 1


async def test_a_call_left_stuck_is_swept_up_and_raised(sent, test_user, test_property):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    session = await _session(test_user.id, test_property.id, status="in_progress", started_at=old)
    async with AsyncSessionLocal() as db:
        assert await call_service.reconcile_stuck_call_sessions(db) >= 1
    async with AsyncSessionLocal() as db:
        assert (await db.get(CallSession, session.id)).call_type == "MISSED_SYSTEM_FAILURE"
    (ticket,) = await _tickets(test_user.id)
    assert ticket.call_session_id == session.id and "never finished" in ticket.detail


def test_the_metrics_collector_can_tell_whether_mira_ever_answered():
    collector = CallMetricsCollector(groq_models=[], openrouter_model="x")
    assert (collector.user_turns, collector.bot_replies) == (0, 0)
    collector._observe(UserStoppedSpeakingFrame())
    collector._observe(UserStoppedSpeakingFrame())
    assert (collector.user_turns, collector.bot_replies) == (2, 0)  # the guest spoke, Mira never did
    collector._observe(BotStartedSpeakingFrame())
    assert collector.bot_replies == 1


def test_the_pipeline_actually_calls_the_alarm():
    """Both pipeline hooks run only on a live call, so nothing else would notice
    one being deleted (a function with a test but no caller is a silent gap)."""
    import inspect
    import re

    from app.voice import pipeline

    crash = inspect.getsource(pipeline._run_pipeline)
    assert 'missed_call_service.spawn_alert(call_session_id, "pipeline_crash")' in crash
    ended = inspect.getsource(pipeline._run_pipeline_inner)
    assert re.search(r'call_metrics\.bot_replies == 0.*?spawn_alert\(call_session_id, "never_answered"\)', ended, re.S)
