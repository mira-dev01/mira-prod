import httpx
import pytest

from app.integrations import email_client


def _configure_resend(monkeypatch):
    monkeypatch.setattr(email_client.settings, "resend_api_key", "re_fake_key")
    monkeypatch.setattr(email_client.settings, "resend_from_email", "mira@example.com")


async def test_send_email_skipped_when_resend_not_configured(monkeypatch):
    monkeypatch.setattr(email_client.settings, "resend_api_key", None)
    result = await email_client.send_email("host@example.com", "Subject", "Body")
    assert result == {"status": "skipped", "reason": "Resend is not configured"}


async def test_send_email_sends_via_resend_and_reports_success(monkeypatch):
    _configure_resend(monkeypatch)
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(200, json={"id": "fake-id"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    result = await email_client.send_email("host@example.com", "Subject", "Body", html_body="<p>Body</p>")

    assert result == {"status": "sent"}
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_fake_key"
    assert captured["json"]["from"] == "Mira <mira@example.com>"
    assert captured["json"]["to"] == ["host@example.com"]
    assert captured["json"]["subject"] == "Subject"
    assert captured["json"]["text"] == "Body"
    assert captured["json"]["html"] == "<p>Body</p>"


async def test_send_email_omits_html_key_when_no_html_body(monkeypatch):
    _configure_resend(monkeypatch)
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return httpx.Response(200, json={"id": "fake-id"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    await email_client.send_email("host@example.com", "Subject", "Body")

    assert "html" not in captured["json"]


async def test_send_email_raises_resend_error_on_non_2xx_response(monkeypatch):
    _configure_resend(monkeypatch)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            return httpx.Response(
                403, json={"message": "domain not verified"}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    with pytest.raises(email_client.ResendError, match="403"):
        await email_client.send_email("host@example.com", "Subject", "Body")


async def test_send_email_passes_a_bounded_timeout_to_httpx_client(monkeypatch):
    """Scale Readiness ("Phase 17"): a slow/misconfigured email API must not
    hang pipeline teardown -- confirms an explicit, bounded default timeout
    reaches the underlying httpx client, not an unbounded wait."""
    _configure_resend(monkeypatch)
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            return httpx.Response(200, json={"id": "fake-id"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    result = await email_client.send_email("host@example.com", "Subject", "Body")

    assert result == {"status": "sent"}
    assert captured["timeout"] == 15.0


async def test_send_email_timeout_is_overridable(monkeypatch):
    _configure_resend(monkeypatch)
    captured = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            return httpx.Response(200, json={"id": "fake-id"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    await email_client.send_email("host@example.com", "Subject", "Body", timeout=5.0)

    assert captured["timeout"] == 5.0
