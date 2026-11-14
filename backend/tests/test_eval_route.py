"""The deployed eval websocket (/api/v1/voice/eval/ws/{token}) must be unreachable unless deliberately switched on.

It runs the full agent pipeline on request, so the bar is the same as for the Exotel webhook token and higher:
a configured token of real length, the right token in the URL, and a database that says eval/test (or an explicit
override). Production fails the last check on its database name alone.
"""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.main import app
from app.voice import eval_mode
from app.voice.eval_mode import MIN_EVAL_ROUTE_TOKEN_LENGTH, eval_route_authorized

TOKEN = "t" * MIN_EVAL_ROUTE_TOKEN_LENGTH
URL = f"/api/v1/voice/eval/ws/{TOKEN}"


@pytest.fixture
def on(monkeypatch):
    """The route switched on, against the test database (its name contains "test")."""
    monkeypatch.setattr(settings, "eval_route_token", TOKEN)
    monkeypatch.delenv("EVAL_ALLOW_ANY_DATABASE", raising=False)


def test_it_is_off_by_default():
    assert settings.eval_route_token == ""
    assert eval_route_authorized(TOKEN) is False
    assert eval_route_authorized("") is False
    assert eval_route_authorized(None) is False


def test_a_token_that_is_too_short_never_switches_it_on(monkeypatch):
    monkeypatch.setattr(settings, "eval_route_token", "short")
    assert eval_route_authorized("short") is False


def test_the_right_token_on_an_eval_database_is_accepted(on):
    assert eval_route_authorized(TOKEN) is True


@pytest.mark.parametrize("given", ["", "wrong", "t" * (MIN_EVAL_ROUTE_TOKEN_LENGTH - 1), "t" * (MIN_EVAL_ROUTE_TOKEN_LENGTH + 1), None])
def test_any_other_token_is_refused(on, given):
    assert eval_route_authorized(given) is False


def test_a_database_that_does_not_say_eval_refuses_even_the_right_token(on, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@db.example.com:5432/postgres")
    assert eval_route_authorized(TOKEN) is False  # how a production database looks to it
    monkeypatch.setenv("EVAL_ALLOW_ANY_DATABASE", "1")
    assert eval_route_authorized(TOKEN) is True  # a deliberate override on a deployed dev database


def test_the_handshake_is_refused_without_the_token_configured():
    with pytest.raises(WebSocketDisconnect) as refused:
        with TestClient(app).websocket_connect(URL):
            pass
    assert refused.value.code == 4401


def test_the_handshake_is_refused_with_the_wrong_token(on):
    with pytest.raises(WebSocketDisconnect) as refused:
        with TestClient(app).websocket_connect("/api/v1/voice/eval/ws/" + "x" * MIN_EVAL_ROUTE_TOKEN_LENGTH):
            pass
    assert refused.value.code == 4401


def test_a_plain_http_request_to_the_path_is_a_404_with_or_without_a_token(on):
    client = TestClient(app)
    assert client.get(URL).status_code == 404
    assert client.get("/api/v1/voice/eval/ws/nope").status_code == 404


def test_an_authorised_connection_reaches_the_eval_pipeline(on, monkeypatch):
    from app.api.v1 import voice

    reached = []

    async def fake_pipeline(websocket):
        reached.append(websocket)
        await websocket.send_text("ready")

    monkeypatch.setattr(voice, "run_eval_voice_pipeline", fake_pipeline)

    with TestClient(app).websocket_connect(URL) as ws:
        assert ws.receive_text() == "ready"
    assert len(reached) == 1


def test_a_refused_connection_never_reaches_the_pipeline(monkeypatch):
    from app.api.v1 import voice

    async def must_not_run(websocket):
        raise AssertionError("the eval pipeline must not start for a refused connection")

    monkeypatch.setattr(voice, "run_eval_voice_pipeline", must_not_run)

    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect(URL):
            pass


def test_the_authorisation_uses_a_constant_time_comparison():
    import inspect

    from app.utils import webhook_auth

    assert "compare_digest" in inspect.getsource(webhook_auth.verify_shared_secret_token)
    assert "verify_shared_secret_token" in inspect.getsource(eval_mode.eval_route_authorized)
