"""The marketing site's public "test the agent" call (app/api/v1/demo_call.py, app/services/demo_call_service.py,
app/voice/demo_mode.py): the grant and limits that stop a stranger spending money, and the guarantee that a demo call
never reaches a person or a phone."""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app.api.v1 import demo_call as demo_api
from app.config import settings
from app.models.demo_call import DemoCallRequest
from app.services import demo_call_service as demo
from app.voice import demo_mode
from app.voice.tool_contract import ToolOutcome, voice_tool

SECRET = "x" * 40


@pytest.fixture
def demo_on(monkeypatch, test_user):
    """The demo call switched on, with the sample host being the test user and the bot check off."""
    monkeypatch.setattr(settings, "demo_host_email", test_user.email)
    monkeypatch.setattr(settings, "demo_call_secret", SECRET)
    monkeypatch.setattr(settings, "demo_turnstile_secret", "")
    monkeypatch.setattr(settings, "demo_lead_notify_email", "")
    monkeypatch.setattr(settings, "demo_call_per_ip_hour", 3)
    monkeypatch.setattr(settings, "demo_call_per_phone_day", 2)
    monkeypatch.setattr(settings, "demo_call_daily_cap", 150)
    monkeypatch.setattr(settings, "demo_call_max_concurrent", 3)
    monkeypatch.setattr(demo.active_calls, "_ids", set())


GOOD = {"name": "Asha Rao", "phone": "+91 98765 43210"}


# --- Visitor input ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("+91 98765 43210", "+919876543210"),
    ("98765-43210", "9876543210"),
    ("(022) 2345 6789", "02223456789"),
    ("12345", None),
    ("abc", None),
    ("", None),
    (None, None),
    ("+1234567890123456", None),
    ("98765 43210; DROP TABLE", None),
])
def test_clean_phone(raw, expected):
    assert demo.clean_phone(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("  Asha   Rao ", "Asha Rao"),
    ("A", None),
    ("", None),
    ("<script>", None),
    ("x" * 81, None),
    ("Rao\x00", None),
])
def test_clean_name(raw, expected):
    assert demo.clean_name(raw) == expected


# --- The call token --------------------------------------------------------------------------------------------------


def test_token_round_trip_and_expiry(monkeypatch):
    monkeypatch.setattr(settings, "demo_host_email", "host@example.com")
    monkeypatch.setattr(settings, "demo_call_secret", SECRET)
    request_id = uuid.uuid4()
    now = time.time()
    token = demo.issue_token(request_id, now=now)
    assert demo.verify_token(token, now=now + 10) == request_id
    assert demo.verify_token(token, now=now + demo.TOKEN_TTL_SECONDS + 1) is None


def test_token_rejects_tampering_and_other_secrets(monkeypatch):
    monkeypatch.setattr(settings, "demo_host_email", "host@example.com")
    monkeypatch.setattr(settings, "demo_call_secret", SECRET)
    request_id = uuid.uuid4()
    token = demo.issue_token(request_id)
    other = uuid.uuid4()
    forged = token.replace(str(request_id), str(other))
    assert demo.verify_token(forged) is None
    assert demo.verify_token(token + "0") is None
    assert demo.verify_token("not-a-token") is None
    assert demo.verify_token(None) is None
    monkeypatch.setattr(settings, "demo_call_secret", "y" * 40)
    assert demo.verify_token(token) is None


def test_no_token_verifies_while_the_feature_is_off(monkeypatch):
    monkeypatch.setattr(settings, "demo_host_email", "host@example.com")
    monkeypatch.setattr(settings, "demo_call_secret", SECRET)
    token = demo.issue_token(uuid.uuid4())
    monkeypatch.setattr(settings, "demo_call_secret", "short")
    assert demo.verify_token(token) is None


# --- Off by default --------------------------------------------------------------------------------------------------


async def test_everything_is_off_until_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_host_email", "")
    monkeypatch.setattr(settings, "demo_call_secret", "")
    config = await client.get("/api/v1/public/demo-call/config")
    assert config.status_code == 200
    assert config.json()["enabled"] is False
    assert (await client.post("/api/v1/public/demo-call/session", json=GOOD)).status_code == 404
    assert (await client.post("/api/v1/public/demo-call/offer", json={"sdp": "x", "type": "offer"})).status_code == 404


async def test_a_short_secret_does_not_switch_it_on(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_host_email", "host@example.com")
    monkeypatch.setattr(settings, "demo_call_secret", "too-short")
    assert (await client.get("/api/v1/public/demo-call/config")).json()["enabled"] is False


# --- Starting a session ----------------------------------------------------------------------------------------------


async def test_session_stores_the_lead_and_returns_a_token(client, demo_on, db_session):
    resp = await client.post("/api/v1/public/demo-call/session", json=GOOD, headers={"x-forwarded-for": "203.0.113.7"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_seconds"] == settings.demo_call_max_seconds
    assert body["ice_servers"]
    assert demo.verify_token(body["token"]) is not None

    rows = (await db_session.scalars(select(DemoCallRequest))).all()
    assert len(rows) == 1
    assert rows[0].name == "Asha Rao" and rows[0].phone == "+919876543210"
    assert "203.0.113.7" not in rows[0].ip_hash  # stored hashed, never raw
    assert rows[0].started_at is None


@pytest.mark.parametrize("payload", [
    {"name": "", "phone": "+919876543210"},
    {"name": "Asha", "phone": "123"},
    {"name": "Asha", "phone": "not a number"},
])
async def test_session_rejects_bad_input(client, demo_on, payload):
    assert (await client.post("/api/v1/public/demo-call/session", json=payload)).status_code == 400


async def test_session_needs_the_bot_check_when_it_is_configured(client, demo_on, monkeypatch):
    monkeypatch.setattr(settings, "demo_turnstile_secret", "secret")

    async def refuse(token, ip):
        return False

    monkeypatch.setattr(demo, "verify_turnstile", refuse)
    resp = await client.post("/api/v1/public/demo-call/session", json=GOOD)
    assert resp.status_code == 400


async def test_turnstile_fails_closed_without_a_token(monkeypatch):
    monkeypatch.setattr(settings, "demo_turnstile_secret", "secret")
    assert await demo.verify_turnstile(None, "203.0.113.7") is False
    monkeypatch.setattr(settings, "demo_turnstile_secret", "")
    assert await demo.verify_turnstile(None, "203.0.113.7") is True


async def test_one_number_is_limited_per_day(client, demo_on):
    for _ in range(2):
        ok = await client.post(
            "/api/v1/public/demo-call/session", json=GOOD, headers={"x-forwarded-for": f"198.51.100.{uuid.uuid4().int % 200}"}
        )
        assert ok.status_code == 200
    third = await client.post("/api/v1/public/demo-call/session", json=GOOD, headers={"x-forwarded-for": "198.51.100.250"})
    assert third.status_code == 429


async def test_one_address_is_limited_per_hour(client, demo_on):
    for i in range(3):
        ok = await client.post(
            "/api/v1/public/demo-call/session",
            json={"name": "Asha", "phone": f"+91987654321{i}"},
            headers={"x-forwarded-for": "203.0.113.9"},
        )
        assert ok.status_code == 200
    fourth = await client.post(
        "/api/v1/public/demo-call/session",
        json={"name": "Asha", "phone": "+919876543299"},
        headers={"x-forwarded-for": "203.0.113.9"},
    )
    assert fourth.status_code == 429


async def test_a_daily_cap_stops_everyone(client, demo_on, monkeypatch):
    monkeypatch.setattr(settings, "demo_call_daily_cap", 2)
    for i in range(2):
        ok = await client.post(
            "/api/v1/public/demo-call/session",
            json={"name": "Asha", "phone": f"+91987654322{i}"},
            headers={"x-forwarded-for": f"203.0.113.{20 + i}"},
        )
        assert ok.status_code == 200
    capped = await client.post(
        "/api/v1/public/demo-call/session",
        json={"name": "Asha", "phone": "+919876543230"},
        headers={"x-forwarded-for": "203.0.113.99"},
    )
    assert capped.status_code == 503


async def test_session_says_so_when_the_sample_host_was_never_seeded(client, demo_on, monkeypatch):
    monkeypatch.setattr(settings, "demo_host_email", "nobody@example.com")
    assert (await client.post("/api/v1/public/demo-call/session", json=GOOD)).status_code == 503


# --- Starting the call -----------------------------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self, ice_servers=None):
        self.closed = False

    async def initialize(self, sdp, type):
        pass

    def get_answer(self):
        return {"sdp": "answer-sdp", "type": "answer"}

    async def disconnect(self):
        self.closed = True


@pytest.fixture
def fake_pipeline(monkeypatch):
    """No real WebRTC and no real agent: record the call that would have started."""
    started: list[dict] = []

    async def fake_run(connection, host, *, max_seconds, on_finished=None):
        started.append({"host": host.email, "max_seconds": max_seconds})
        if on_finished is not None:
            await on_finished()

    monkeypatch.setattr(demo_api, "SmallWebRTCConnection", _FakeConnection)
    monkeypatch.setattr(demo_api, "run_browser_demo_pipeline", fake_run)
    return started


async def _token(client) -> str:
    resp = await client.post(
        "/api/v1/public/demo-call/session", json=GOOD, headers={"x-forwarded-for": f"203.0.113.{uuid.uuid4().int % 200}"}
    )
    assert resp.status_code == 200
    return resp.json()["token"]


def _offer(token):
    return {"json": {"sdp": "v=0", "type": "offer"}, "headers": {"Authorization": f"Bearer {token}"}}


async def test_offer_needs_a_valid_token(client, demo_on, fake_pipeline):
    assert (await client.post("/api/v1/public/demo-call/offer", json={"sdp": "v=0", "type": "offer"})).status_code == 401
    assert (await client.post("/api/v1/public/demo-call/offer", **_offer("garbage"))).status_code == 401
    assert fake_pipeline == []


async def test_offer_starts_the_demo_pipeline_for_the_sample_host_only(client, demo_on, fake_pipeline, test_user, db_session):
    token = await _token(client)
    resp = await client.post("/api/v1/public/demo-call/offer", **_offer(token))
    assert resp.status_code == 200
    assert resp.json() == {"sdp": "answer-sdp", "type": "answer"}
    await asyncio.sleep(0.2)  # the pipeline runs as a background task
    assert fake_pipeline == [{"host": test_user.email, "max_seconds": settings.demo_call_max_seconds}]

    db_session.expire_all()
    row = (await db_session.scalars(select(DemoCallRequest))).one()
    assert row.started_at is not None and row.ended_at is not None  # the fake pipeline finished at once
    assert len(demo.active_calls) == 0


async def test_a_token_starts_one_call_only(client, demo_on, fake_pipeline):
    token = await _token(client)
    assert (await client.post("/api/v1/public/demo-call/offer", **_offer(token))).status_code == 200
    again = await client.post("/api/v1/public/demo-call/offer", **_offer(token))
    assert again.status_code == 409
    assert len(fake_pipeline) == 1


async def test_a_busy_line_refuses_without_spending_the_token(client, demo_on, fake_pipeline, monkeypatch, db_session):
    token = await _token(client)
    monkeypatch.setattr(settings, "demo_call_max_concurrent", 0)
    busy = await client.post("/api/v1/public/demo-call/offer", **_offer(token))
    assert busy.status_code == 503
    assert fake_pipeline == []
    monkeypatch.setattr(settings, "demo_call_max_concurrent", 3)
    retry = await client.post("/api/v1/public/demo-call/offer", **_offer(token))
    assert retry.status_code == 200


# --- Nothing reaches a person or a phone -----------------------------------------------------------------------------


class _Params:
    def __init__(self):
        self.tool_call_id = "tc"
        self.calls = []

    async def result_callback(self, result, properties=None):
        self.calls.append(result)


def _recording_tool(name: str, ran: list):
    async def tool(params, message: str = "hi"):
        ran.append(name)
        await params.result_callback("really sent")

    tool.__name__ = name
    return voice_tool()(tool)


async def test_side_effect_tools_are_simulated_on_a_demo_call():
    ran: list[str] = []
    for name in sorted(demo_mode.SIMULATED_TOOLS):
        params = _Params()
        with demo_mode.demo_scope():
            await _recording_tool(name, ran)(params, message="hello")
        text = str(params.calls[0])
        assert "Demo call" in text, name
        assert "really sent" not in text, name
    assert ran == [], "a simulated tool body must never run on a demo call"


async def test_the_same_tools_run_normally_outside_a_demo_call():
    ran: list[str] = []
    params = _Params()
    await _recording_tool("send_whatsapp", ran)(params, message="hello")
    assert ran == ["send_whatsapp"]
    assert "really sent" in str(params.calls[0])


async def test_other_tools_still_run_on_a_demo_call():
    ran: list[str] = []
    params = _Params()
    with demo_mode.demo_scope():
        await _recording_tool("check_calendar", ran)(params, message="hello")
    assert ran == ["check_calendar"]


def test_demo_mode_ends_with_its_scope():
    assert demo_mode.is_demo() is False
    with demo_mode.demo_scope():
        assert demo_mode.is_demo() is True
    assert demo_mode.is_demo() is False


# Tools that only read or write the sample host's own data and tell no one. Every voice tool must be in exactly one of
# this set or demo_mode.SIMULATED_TOOLS, so adding a tool forces a decision about whether a demo call may run it.
_SAFE_ON_A_DEMO_CALL = frozenset(
    {
        "check_calendar",
        "decline_irrelevant_call",
        "end_call",
        "get_payment_status",
        "get_pricing",
        "lookup_booking",
        "negotiate_rate",
        "recommend_properties",
        "search_faq",
        "update_lead",
    }
)


def test_every_voice_tool_is_either_simulated_or_known_safe_on_a_demo_call():
    """The guard names tools by string. A renamed tool would leave its replacement unguarded, and a new tool
    that messages someone would reach a stranger's call. Both fail here instead."""
    from tests.test_tool_contract import _tools

    real = set(_tools(payments_enabled=True, spacez_enabled=True))
    assert demo_mode.SIMULATED_TOOLS <= real, f"not a real tool: {sorted(demo_mode.SIMULATED_TOOLS - real)}"
    unclassified = real - demo_mode.SIMULATED_TOOLS - _SAFE_ON_A_DEMO_CALL
    assert not unclassified, f"decide whether a demo call may run these: {sorted(unclassified)}"
    assert not (demo_mode.SIMULATED_TOOLS & _SAFE_ON_A_DEMO_CALL)


def test_tool_outcome_is_what_the_guard_delivers():
    assert ToolOutcome.ok("x").status == "ok"
