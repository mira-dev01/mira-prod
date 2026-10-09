"""Covers app/integrations/billing_clients.py parsing + fail-open behavior
against mocked provider HTTP responses (no real network)."""

import httpx
import pytest

from app.config import settings
from app.integrations import billing_clients


@pytest.fixture
def mock_http(monkeypatch):
    routes = {}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, (status, body) in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(billing_clients.httpx, "AsyncClient", factory)
    billing_clients._local_cache.clear()
    return routes


@pytest.fixture(autouse=True)
def _no_redis_cache(monkeypatch):
    async def _get(key):
        return None

    async def _set(key, value, ttl):
        return None

    monkeypatch.setattr(billing_clients.redis_client, "cache_get_json", _get)
    monkeypatch.setattr(billing_clients.redis_client, "cache_set_json", _set)


async def test_twilio_balance(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    mock_http["https://api.twilio.com/2010-04-01/Accounts/AC123/Balance.json"] = (200, {"balance": "12.50", "currency": "USD"})

    result = await billing_clients.fetch_balance("twilio", refresh=True)
    assert result["status"] == "ok"
    assert result["balance"] == 12.5
    assert result["currency"] == "USD"


async def test_openrouter_remaining_credits(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    mock_http["https://openrouter.ai/api/v1/credits"] = (200, {"data": {"total_credits": 20, "total_usage": 5.25}})

    result = await billing_clients.fetch_balance("openrouter", refresh=True)
    assert result["balance"] == 14.75
    assert result["limit"] == 20


async def test_cloudinary_credits(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "cloudinary_cloud_name", "demo")
    monkeypatch.setattr(settings, "cloudinary_api_key", "k")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "s")
    mock_http["https://api.cloudinary.com/v1_1/demo/usage"] = (
        200,
        {"plan": "Free", "credits": {"usage": 3.2, "limit": 25, "used_percent": 12.8}},
    )

    result = await billing_clients.fetch_balance("cloudinary", refresh=True)
    assert result["used"] == 3.2
    assert result["balance"] == 21.8


async def test_brightdata_permission_error_is_explained(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "bright_data_api_key", "k")
    mock_http["https://api.brightdata.com/customer/balance"] = (403, {"error": "forbidden"})

    result = await billing_clients.fetch_balance("brightdata", refresh=True)
    assert result["status"] == "error"
    assert "permission" in result["detail"]


async def test_unexpected_payload_reports_keys_instead_of_wrong_number(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "searchapi_api_key", "k")
    mock_http["https://www.searchapi.io/api/v1/me"] = (200, {"something_else": 1})

    result = await billing_clients.fetch_balance("searchapi", refresh=True)
    assert result["status"] == "error"
    assert "something_else" in result["detail"]


async def test_neon_compute_hours(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "neon_api_key", "k")
    monkeypatch.setattr(settings, "neon_project_id", "proj-1")
    mock_http["https://console.neon.tech/api/v2/projects/proj-1"] = (
        200,
        {"project": {"compute_time_seconds": 7200, "synthetic_storage_size": 1073741824}},
    )

    result = await billing_clients.fetch_balance("neon", refresh=True)
    assert result["used"] == 2.0
    assert "1.00 GB" in result["detail"]


async def test_not_configured_makes_no_request(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "neon_api_key", None)
    result = await billing_clients.fetch_balance("neon", refresh=True)
    assert result["status"] == "not_configured"


async def test_provider_http_error_fails_open(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    mock_http["https://openrouter.ai/api/v1/credits"] = (500, {})

    result = await billing_clients.fetch_balance("openrouter", refresh=True)
    assert result["status"] == "error"
    assert "HTTP 500" in result["detail"]


async def test_successful_result_is_cached_in_process(mock_http, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    mock_http["https://openrouter.ai/api/v1/credits"] = (200, {"data": {"total_credits": 10, "total_usage": 1}})
    first = await billing_clients.fetch_balance("openrouter", refresh=True)
    mock_http["https://openrouter.ai/api/v1/credits"] = (200, {"data": {"total_credits": 10, "total_usage": 9}})

    second = await billing_clients.fetch_balance("openrouter")
    assert second["balance"] == first["balance"] == 9
