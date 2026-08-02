import respx
from httpx import Response

from app.config import settings
from app.integrations import exotel_client


async def test_hangup_call_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "exotel_sid", None)
    result = await exotel_client.hangup_call("some-call-sid")
    assert result == {"status": "skipped", "reason": "Exotel credentials not configured"}


@respx.mock
async def test_hangup_call_posts_status_completed(monkeypatch):
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    route = respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-123.json").mock(
        return_value=Response(200, json={"Call": {"Sid": "call-sid-123", "Status": "completed"}})
    )

    result = await exotel_client.hangup_call("call-sid-123")

    assert route.called
    sent_body = route.calls.last.request.content.decode()
    assert "Status=completed" in sent_body
    assert result == {"Call": {"Sid": "call-sid-123", "Status": "completed"}}
