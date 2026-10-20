"""Service health monitoring: the in-memory evaluation rules
(app/observability/health.py), the passive httpx hook, and the incident /
alert lifecycle against the real test DB (app/services/
health_monitor_service.py) -- including that a redeploy mid-incident never
re-sends an alert."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import email_client
from app.models.service_incident import ServiceIncident
from app.observability import health, http_hook
from app.services import admin_monitor_service, health_monitor_service
from tests.test_admin_api import admin_headers  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
async def _clean_state():
    health.reset_for_tests()
    health_monitor_service.reset_for_tests()
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ServiceIncident))
        await db.commit()
    yield
    health.reset_for_tests()
    health_monitor_service.reset_for_tests()


@pytest.fixture
def sent_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(to, subject, body, html_body=None, timeout=15.0):
        sent.append({"to": to, "subject": subject, "body": body, "html": html_body})
        return {"status": "sent"}

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    monkeypatch.setattr(settings, "health_alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_emails", "a@example.com,b@example.com")
    return sent


def _errors(service: str, n: int, message: str = "boom") -> None:
    for _ in range(n):
        health.record(service, "error", error=message)


def _clear_window(service: str) -> None:
    health._tracks[service].events.clear()


# --------------------------------------------------------------------------
# evaluation rules
# --------------------------------------------------------------------------


def test_record_never_raises_on_garbage():
    health.record("voice_pipeline", "error", error=object(), latency_ms="not-a-number")  # type: ignore[arg-type]
    health.record_probe("voice_pipeline", "down", latency_ms=None, error=None)


def test_passive_errors_drive_down_and_degraded():
    _errors("voice_pipeline", 5)
    assert [t.new for t in health.evaluate() if t.service == "voice_pipeline"] == ["down"]

    health.reset_for_tests()
    for _ in range(8):
        health.record("api", "ok")
    _errors("api", 2)
    health.evaluate()
    assert health.state_of("api") == "degraded"


def test_isolated_failure_is_not_an_outage():
    for _ in range(20):
        health.record("api", "ok")
    _errors("api", 1)
    health.evaluate()
    assert health.state_of("api") == "up"


def test_recovered_reconnects_degrade_but_dont_down():
    for _ in range(3):
        health.record("sarvam_stt", "warn", error="dropped", op="reconnect")
    health.evaluate()
    assert health.state_of("sarvam_stt") in ("degraded", "not_configured")


def test_recovery_needs_consecutive_good_evaluations():
    _errors("voice_pipeline", 5)
    health.evaluate()
    _clear_window("voice_pipeline")
    health.record("voice_pipeline", "ok")
    health.evaluate()  # first better evaluation is held
    assert health.state_of("voice_pipeline") == "down"
    assert [t.new for t in health.evaluate() if t.service == "voice_pipeline"] == ["up"]


def test_single_failed_probe_is_a_blip_two_are_down():
    health.record_probe("postgres", "down", error="timeout")
    health.evaluate()
    assert health.state_of("postgres") == "up"
    health.record_probe("postgres", "down", error="timeout")
    health.evaluate()
    assert health.state_of("postgres") == "down"


def test_confirmed_probe_is_down_immediately():
    health.record_probe("credits:sarvam", "down", error="2% left", confirmed=True)
    health.evaluate()
    assert health.state_of("credits:sarvam") == "down"


def test_no_new_evidence_holds_last_known_state():
    _errors("voice_pipeline", 5)
    health.evaluate()
    _clear_window("voice_pipeline")
    health.evaluate()
    health.evaluate()
    assert health.state_of("voice_pipeline") == "down"


def test_unconfigured_service_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "searchapi_api_key", None)
    _errors("searchapi", 5)
    health.evaluate()
    assert health.state_of("searchapi") == "not_configured"


# --------------------------------------------------------------------------
# passive httpx hook
# --------------------------------------------------------------------------


def test_host_mapping_and_status_classification():
    assert http_hook.service_for_host("api.groq.com") == "groq"
    assert http_hook.service_for_host("conversations.twilio.com") == "twilio"
    assert http_hook.service_for_host("example.com") is None
    assert http_hook.classify_status(503)[0] == "error"
    # Throttled != broken (Groq's fallback chain retries the next model).
    assert http_hook.classify_status(429) == ("warn", "rate_limited")
    assert http_hook.classify_status(401) == ("error", "auth_failed")
    # One bad request (a WhatsApp 24h-window rejection, an unknown listing)
    # isn't the provider failing.
    assert http_hook.classify_status(400)[0] == "ok"
    assert http_hook.classify_status(404)[0] == "ok"


async def test_hook_records_real_requests_but_not_probes():
    http_hook.install()
    status = {"code": 503}
    transport = httpx.MockTransport(lambda request: httpx.Response(status["code"], text="upstream down"))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://api.resend.com/emails")
        status["code"] = 200
        await client.get("https://api.resend.com/emails")
        with health.suppress_passive():
            await client.get("https://api.resend.com/emails")
        await client.get("https://unrelated.example.com/")

    window = next(s for s in health.snapshot() if s["key"] == "resend")["window"]
    assert (window["ok"], window["errors"]) == (1, 1)
    assert "HTTP 503" in health.last_error("resend")["message"]


async def test_hook_records_transport_errors_and_reraises():
    http_hook.install()

    def _boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_boom)) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.post("https://api.twilio.com/2010-04-01/Accounts/x/Messages.json")
    assert health.last_error("twilio")["kind"] == "timeout"


async def test_rate_limits_degrade_but_never_down_a_service():
    http_hook.install()
    transport = httpx.MockTransport(lambda request: httpx.Response(429, text="rate limited"))
    async with httpx.AsyncClient(transport=transport) as client:
        for _ in range(10):
            await client.post("https://api.groq.com/openai/v1/chat/completions")
    health.evaluate()
    assert health.state_of("groq") in ("degraded", "not_configured")


async def test_balance_lookups_are_not_service_traffic(monkeypatch):
    from app.integrations import billing_clients

    http_hook.install()
    monkeypatch.setattr(settings, "bright_data_api_key", "k")
    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(401, text="no billing permission"))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(billing_clients.httpx, "AsyncClient", _client)
    for _ in range(3):
        result = await billing_clients.fetch_balance("brightdata", refresh=True)
        assert result["status"] == "error"
    assert next(s for s in health.snapshot() if s["key"] == "bright_data")["window"]["requests"] == 0


def test_degraded_probe_carries_its_reason():
    health.record_probe("groq", "degraded", error="Primary model x failing; serving from y")
    assert health.last_error("groq")["message"] == "Primary model x failing; serving from y"


# --------------------------------------------------------------------------
# incidents + alerts
# --------------------------------------------------------------------------


async def _incidents() -> list[ServiceIncident]:
    async with AsyncSessionLocal() as db:
        return list((await db.scalars(select(ServiceIncident).order_by(ServiceIncident.opened_at))).all())


async def test_down_alerts_once_then_resolves_with_recovery_email(sent_emails):
    _errors("voice_pipeline", 5, "pipeline crashed: KeyError 'x'")
    await health_monitor_service.tick()

    [incident] = await _incidents()
    assert (incident.service, incident.severity, incident.resolved_at) == ("voice_pipeline", "down", None)
    assert incident.error_count == 5
    assert incident.down_alert_sent
    assert [e["to"] for e in sent_emails] == ["a@example.com", "b@example.com"]
    assert "URGENT" in sent_emails[0]["subject"] and "Voice pipeline is DOWN" in sent_emails[0]["subject"]
    assert "KeyError" in sent_emails[0]["body"]

    await health_monitor_service.tick()
    await health_monitor_service.tick()
    assert len(sent_emails) == 2  # deduped

    _clear_window("voice_pipeline")
    health.record("voice_pipeline", "ok")
    await health_monitor_service.tick()
    await health_monitor_service.tick()

    [incident] = await _incidents()
    assert incident.resolved_at is not None and incident.current_state == "up"
    assert len(sent_emails) == 4
    assert sent_emails[-1]["subject"].startswith("🟢 Resolved")


async def test_redeploy_mid_incident_does_not_realert(sent_emails):
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()
    assert len(sent_emails) == 2

    # New process: in-memory state gone, open incident still in the DB.
    health.reset_for_tests()
    health_monitor_service.reset_for_tests()
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()

    assert len(await _incidents()) == 1
    assert len(sent_emails) == 2


async def test_degraded_waits_before_alerting(sent_emails):
    for _ in range(8):
        health.record("api", "ok")
    _errors("api", 2)
    await health_monitor_service.tick()
    assert sent_emails == []

    async with AsyncSessionLocal() as db:
        incident = (await db.scalars(select(ServiceIncident))).one()
        incident.opened_at -= timedelta(minutes=settings.health_degraded_alert_minutes + 1)
        await db.commit()
    await health_monitor_service.tick()
    assert len(sent_emails) == 2 and "degraded" in sent_emails[0]["subject"]


async def test_alerts_off_records_incident_but_sends_nothing(sent_emails, monkeypatch):
    monkeypatch.setattr(settings, "health_alerts_enabled", False)
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()
    assert len(await _incidents()) == 1
    assert sent_emails == []


async def test_failed_send_is_retried_later(sent_emails, monkeypatch):
    async def _down(*args, **kwargs):
        raise email_client.ResendError("send failed (503)")

    monkeypatch.setattr(email_client, "send_email", _down)
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()
    [incident] = await _incidents()
    assert not incident.down_alert_sent


async def test_db_outage_still_alerts_from_memory_then_reconciles(sent_emails, monkeypatch):
    def _unreachable():
        raise ConnectionRefusedError("database unreachable")

    monkeypatch.setattr(health_monitor_service, "AsyncSessionLocal", _unreachable)
    _errors("postgres", 5, "connection refused")
    await health_monitor_service.tick()
    assert len(sent_emails) == 2 and "Postgres" in sent_emails[0]["subject"]
    await health_monitor_service.tick()
    assert len(sent_emails) == 2  # deduped in memory

    # DB reachable again, service still failing: recorded, not re-alerted.
    monkeypatch.setattr(health_monitor_service, "AsyncSessionLocal", AsyncSessionLocal)
    await health_monitor_service.tick()
    [incident] = await _incidents()
    assert incident.service == "postgres" and incident.down_alert_sent
    assert len(sent_emails) == 2

    _clear_window("postgres")
    health.record("postgres", "ok")
    await health_monitor_service.tick()
    await health_monitor_service.tick()
    assert (await _incidents())[0].resolved_at is not None
    assert sent_emails[-1]["subject"].startswith("🟢 Resolved")


# --------------------------------------------------------------------------
# credits + snapshot + digest
# --------------------------------------------------------------------------


def _card(account, **fields):
    return {"account": account, "label": account.title(), "kind": "prepaid", "currency": "INR", **fields}


async def test_credits_map_to_health_states(monkeypatch):
    async def _balances(db, refresh=False):
        return [
            _card("sarvam", status="ok", level="critical", balance=120.0, remaining_share=0.04, days_left=1.2),
            _card("exotel", status="ok", level="low", balance=900.0, remaining_share=0.15),
            _card("groq", status="ok", level="ok", used=3.0, limit=50.0),
            _card("resend", status="not_set", detail="Enter the current balance in Settings."),
            _card("twilio", status="error", detail="HTTP 500"),
        ]

    monkeypatch.setattr(admin_monitor_service, "balances", _balances)
    await health_monitor_service.credits_cycle()
    health.evaluate()

    states = {s["key"]: s["state"] for s in health.snapshot()}
    assert states["credits:sarvam"] == "down"
    assert states["credits:exotel"] == "degraded"
    assert states["credits:groq"] == "up"
    assert states["credits:resend"] == "not_configured"
    assert states["credits:twilio"] == "unknown"  # balance API failed != ran out
    assert "4% left" in health.last_error("credits:sarvam")["message"]


async def test_credits_alert_wording_and_no_reminders(sent_emails, monkeypatch):
    async def _balances(db, refresh=False):
        return [_card("sarvam", label="Sarvam AI", status="ok", level="critical", balance=50.0, remaining_share=0.02, days_left=0.5)]

    monkeypatch.setattr(admin_monitor_service, "balances", _balances)
    await health_monitor_service.credits_cycle()
    await health_monitor_service.tick()
    assert len(sent_emails) == 2
    assert "credits critically low" in sent_emails[0]["subject"] and "DOWN" not in sent_emails[0]["subject"]
    assert "2% left" in sent_emails[0]["html"]

    # Hours later, still critical and re-checked many times: no reminder spam.
    async with AsyncSessionLocal() as db:
        incident = (await db.scalars(select(ServiceIncident))).one()
        incident.last_alert_at -= timedelta(hours=5)
        await db.commit()
    for _ in range(3):
        await health_monitor_service.credits_cycle()
        await health_monitor_service.tick()
    assert len(sent_emails) == 2


async def test_open_incident_durations_are_live(monkeypatch):
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()
    async with AsyncSessionLocal() as db:
        incident = (await db.scalars(select(ServiceIncident))).one()
        incident.opened_at -= timedelta(hours=1)
        incident.detail = {"down_since": (incident.opened_at).isoformat()}
        await db.commit()
        await health_monitor_service._refresh_cache(db)

    later = health_monitor_service._utcnow() + timedelta(minutes=30)
    monkeypatch.setattr(health_monitor_service, "_utcnow", lambda: later)
    [live] = health_monitor_service.snapshot()["incidents"]
    assert live["duration_s"] >= 90 * 60 - 5
    assert live["down_seconds"] >= 90 * 60 - 5
    assert not any(k.startswith("_") for k in live)


async def test_snapshot_shape_and_overall():
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()
    snap = health_monitor_service.snapshot()
    assert snap["overall"] == "down"
    assert {"environment", "groups", "incidents", "counts", "alerts_enabled"} <= snap.keys()
    service = next(s for g in snap["groups"] for s in g["services"] if s["key"] == "voice_pipeline")
    assert service["open_incident_id"] == snap["incidents"][0]["id"]
    assert service["availability_24h"] is not None


async def test_daily_digest_lists_credits_and_incidents(sent_emails, monkeypatch):
    async def _balances(db, refresh=False):
        return [_card("sarvam", status="ok", level="low", balance=900.0, remaining_share=0.15, days_left=4.0)]

    monkeypatch.setattr(admin_monitor_service, "balances", _balances)
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()
    sent_emails.clear()

    result = await health_monitor_service.send_daily_digest(force=True)
    assert result["status"] == "sent"
    assert len(sent_emails) == 2
    email = sent_emails[0]
    assert "Mira daily health" in email["subject"] and "1 down" in email["subject"] and "low credits: Sarvam" in email["subject"]
    assert "15% left" in email["html"] and "Voice pipeline" in email["html"]


async def test_admin_health_endpoints(client, admin_headers, sent_emails):  # noqa: F811
    _errors("voice_pipeline", 5)
    await health_monitor_service.tick()

    resp = await client.get("/api/v1/admin/health", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["overall"] == "down"

    assert (await client.get("/api/v1/admin/health")).status_code in (401, 403)

    digest = await client.post("/api/v1/admin/health/digest", headers=admin_headers)
    assert digest.status_code == 200 and digest.json()["status"] == "sent"


async def test_api_middleware_tracks_5xx_and_crashes():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from app.observability.middleware import ObservabilityMiddleware

    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/fail")
    async def fail():
        return JSONResponse({"detail": "nope"}, status_code=502)

    @app.get("/crash")
    async def crash():
        raise RuntimeError("handler exploded")

    @app.get("/health")
    async def liveness():
        return JSONResponse({}, status_code=500)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/ok")
        await c.get("/fail")
        await c.get("/crash")
        await c.get("/health")  # untracked

    window = next(s for s in health.snapshot() if s["key"] == "api")["window"]
    assert (window["ok"], window["errors"]) == (1, 2)
    assert "handler exploded" in health.last_error("api")["message"]
