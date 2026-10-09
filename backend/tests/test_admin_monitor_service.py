"""Covers app/services/admin_monitor_service.py aggregation against a real
test DB. Calls are placed on fixed future dates so the scope window is exact."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations import billing_clients
from app.models.call_quality_event import AUDIO_INPUT_TELEMETRY_RULE, CALL_METRICS_RULE, CallQualityEvent
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.service_usage_event import ServiceUsageEvent
from app.services import admin_monitor_service as monitor
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

DAY = datetime(2031, 3, 10, 9, 0, tzinfo=timezone.utc)
SCOPE = monitor.Scope(since=datetime(2031, 3, 10, tzinfo=timezone.utc), until=datetime(2031, 3, 11, tzinfo=timezone.utc))


def _call(user, prop, *, minutes=2.0, call_type="BOOKING_LEAD", exotel=True, caller="+919999999999", offset=0):
    start = DAY + timedelta(minutes=offset)
    return CallSession(
        id=uuid.uuid4(),
        user_id=user.id,
        property_id=prop.id,
        exotel_call_id=f"ex-{uuid.uuid4().hex[:10]}" if exotel else None,
        caller_number=caller,
        status="completed",
        call_type=call_type,
        started_at=start,
        ended_at=start + timedelta(minutes=minutes),
        created_at=start,
    )


def _telemetry(call, rule, summary):
    return CallQualityEvent(
        call_session_id=call.id,
        rule=rule,
        severity="INFO",
        confidence=1.0,
        turn_index=0,
        processing_time_ms=1.0,
        metadata_json={"summary": summary, "segments": [], "barge_ins": []},
        created_at=call.created_at,
    )


@pytest.fixture
async def seeded(db_session, test_user, test_property):
    engaged = _call(test_user, test_property, minutes=2.5)
    engaged_no_lead = _call(test_user, test_property, minutes=1.2, offset=10)
    busy = _call(test_user, test_property, minutes=0, call_type="MISSED_AGENT_BUSY", offset=20)
    browser = _call(test_user, test_property, exotel=False, caller=BROWSER_TEST_CALLER_NUMBER, offset=30)
    db_session.add_all([engaged, engaged_no_lead, busy, browser])
    await db_session.flush()
    db_session.add_all(
        [
            _telemetry(
                engaged,
                CALL_METRICS_RULE,
                {
                    "user_turns": 6,
                    "end_frame": "EndFrame",
                    "end_reason": None,
                    "tool_calls": {"get_pricing": 1, "update_lead": 1},
                    "price_quoted": True,
                    "negotiated": False,
                    "engaged_pricing_tools": True,
                    "response_latency_s": [1.0, 2.0],
                    "llm": [{"provider": "groq", "model": "openai/gpt-oss-120b", "completions": 4, "prompt_tokens": 1000, "completion_tokens": 100}],
                },
            ),
            _telemetry(
                engaged_no_lead,
                CALL_METRICS_RULE,
                {
                    "user_turns": 3,
                    "end_frame": "CancelFrame",
                    "end_reason": None,
                    "tool_calls": {"check_calendar": 1},
                    "price_quoted": False,
                    "engaged_pricing_tools": True,
                    "response_latency_s": [3.0],
                },
            ),
            _telemetry(
                engaged,
                AUDIO_INPUT_TELEMETRY_RULE,
                {
                    "segments_total": 10,
                    "short_segments": 3,
                    "barge_ins_total": 4,
                    "phantom_barge_ins": 2,
                    "barge_ins_sarvam_stt": 3,
                    "guard_triggers": 2,
                    "guard_triggers_short": 2,
                    "shadow_echo_drop": 1,
                    "lang_counts": {"hi-IN": 7, "en-IN": 2},
                    "lang_switches": 1,
                    "sarvam_vad_events": {"START_SPEECH": 5},
                    "est_snr_db": 25.0,
                },
            ),
            Lead(user_id=test_user.id, call_session_id=engaged.id, guest_name="Ria", escalated=True, status="booked"),
            Notification(
                call_session_id=engaged.id,
                channel="escalation",
                message="x",
                urgency="high",
                created_at=DAY + timedelta(minutes=3),
                responded_at=DAY + timedelta(minutes=13),
            ),
            ServiceUsageEvent(service="sarvam_stt", unit="audio_seconds", quantity=3600, user_id=test_user.id, created_at=DAY),
            ServiceUsageEvent(service="sarvam_tts", unit="characters", quantity=10000, user_id=test_user.id, created_at=DAY),
            ServiceUsageEvent(
                service="groq", unit="prompt_tokens", quantity=1_000_000, model="openai/gpt-oss-120b", created_at=DAY
            ),
        ]
    )
    await db_session.commit()
    return {"engaged": engaged, "engaged_no_lead": engaged_no_lead, "busy": busy, "browser": browser}


async def test_funnel_and_outcomes(db_session, seeded):
    data = await monitor.conversations(db_session, SCOPE)
    funnel = {s["key"]: s["count"] for s in data["funnel"]}

    assert funnel["calls"] == 3  # browser test call excluded by default
    assert funnel["answered"] == 2  # busy-rejected call is not answered
    assert funnel["engaged"] == 2
    assert funnel["lead"] == 1
    assert funnel["price_quoted"] == 1
    assert funnel["booked"] == 1
    assert data["outcomes"]["escalated"] == 1
    ends = {e["end"]: e["count"] for e in data["outcomes"]["by_end"]}
    assert ends == {"mira_ended": 1, "guest_hung_up": 1, "busy_rejected": 1}
    assert data["language"]["segments_by_language"][0] == {"language": "hi-IN", "segments": 7}


async def test_include_test_calls_toggle(db_session, seeded):
    scope = monitor.Scope(since=SCOPE.since, until=SCOPE.until, include_test_calls=True)
    data = await monitor.conversations(db_session, scope)
    assert data["funnel"][0]["count"] == 4


async def test_lead_safety_flags_engaged_call_without_lead(db_session, seeded):
    data = await monitor.leads_and_recovery(db_session, SCOPE)

    assert data["lead_safety"]["engaged_without_lead"] == 1
    assert data["lead_safety"]["engaged_without_lead_call_ids"] == [str(seeded["engaged_no_lead"].id)]
    assert data["lead_safety"]["leads_via_update_lead"] == 1
    assert data["busy_recovery"]["busy_rejections"] == 1
    assert data["escalations"]["total"] == 1
    assert data["escalations"]["response_minutes"]["p50"] == 10.0


async def test_performance_latency_and_models(db_session, seeded):
    data = await monitor.performance(db_session, SCOPE)
    assert data["latency"]["response_s"]["samples"] == 3
    assert data["latency"]["response_s"]["p50"] == 2.0
    assert data["llm"]["models"][0]["model"] == "openai/gpt-oss-120b"


async def test_audio_decisions_need_minimum_sample(db_session, seeded):
    data = await monitor.audio(db_session, SCOPE)
    assert data["coverage"]["calls_with_audio_telemetry"] == 1
    assert data["interruptions"]["phantom_rate"] == 0.5
    assert data["guard"]["short_share_of_triggers"] == 1.0
    assert {d["verdict"] for d in data["decisions"]} == {"insufficient_data"}


async def test_usage_costs_with_default_prices_and_derived_minutes(db_session, seeded):
    data = await monitor.usage(db_session, SCOPE)
    accounts = {a["account"]: a for a in data["by_account"]}

    # Sarvam: 1h STT at Rs30/h + 10k chars at Rs30/10k = Rs60.
    assert accounts["sarvam"]["cost_inr"] == pytest.approx(60.0)
    # Groq: 1M prompt tokens at $0.15/M, converted at the default USD->INR rate.
    assert accounts["groq"]["cost"] == pytest.approx(0.15)
    # Exotel minutes are derived from durations: ceil(2.5)=3 + ceil(1.2)=2 + busy=1.
    exotel = [r for r in data["rows"] if r["service"] == "exotel"][0]
    assert exotel["quantity"] == 6
    assert data["daily"][0]["date"] == "2031-03-10"
    assert data["daily"][0]["cost_inr"] == pytest.approx(data["totals"]["cost_inr"])


async def test_hosts_breakdown(db_session, seeded, test_user):
    data = await monitor.hosts(db_session, SCOPE)
    host = data["hosts"][0]
    assert host["user_id"] == str(test_user.id)
    assert host["calls"] == 3
    assert host["busy_rejected"] == 1
    assert host["cost_inr"] > 0


async def test_prepaid_balance_is_amount_minus_spend_since_entry(db_session, test_user, monkeypatch):
    async def _stub(service, refresh=False):
        return billing_clients._result(service, "not_configured")

    monkeypatch.setattr(billing_clients, "fetch_balance", _stub)
    await monitor.update_service_setting(db_session, "sarvam", admin_email="a@b.c", prepaid_amount=1000)
    # Usage metered AFTER the balance was entered counts against it.
    db_session.add(
        ServiceUsageEvent(service="sarvam_stt", unit="audio_seconds", quantity=7200, created_at=datetime.now(timezone.utc))
    )
    await db_session.commit()

    cards = {c["account"]: c for c in await monitor.balances(db_session)}
    assert cards["sarvam"]["balance"] == pytest.approx(940.0)
    assert cards["sarvam"]["level"] == "ok"
    assert cards["exotel"]["status"] == "not_set"
    assert cards["twilio"]["status"] == "not_configured"


async def test_unit_price_override_and_unknown_account(db_session):
    await monitor.update_service_setting(db_session, "exotel", admin_email="a@b.c", unit_prices={"minutes": 0.6})
    settings_rows = {s["account"]: s for s in await monitor.list_service_settings(db_session)}
    assert settings_rows["exotel"]["unit_prices"]["minutes"] == 0.6
    with pytest.raises(KeyError):
        await monitor.update_service_setting(db_session, "nope", admin_email="a@b.c")
