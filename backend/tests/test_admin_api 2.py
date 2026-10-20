"""End-to-end wiring of the /admin data API: real HTTP (ASGI) requests with
a real admin token against seeded data -- every route the frontend calls
must answer 200 with the top-level shape frontend/src/lib/admin-api.ts
expects. Payloads are optionally dumped (ADMIN_PAYLOAD_DUMP_DIR) so the
TypeScript contract can be diffed against real responses."""

import json
import os

import pytest

from app.auth.admin import issue_admin_token
from app.config import settings
from app.integrations import billing_clients
from tests.test_admin_monitor_service import seeded  # noqa: F401  (fixture)

ADMIN = "abhayatrivedi2005@gmail.com"
RANGE = "start_date=2031-03-10&end_date=2031-03-10"

EXPECTED_KEYS = {
    "overview": {"kpis", "funnel", "daily_cost", "decisions", "coverage"},
    "audio": {"coverage", "decisions", "interruptions", "segments", "shadow", "guard", "levels", "stt_latency_s", "top_short_texts", "overhead_ms"},
    "conversations": {"coverage", "funnel", "outcomes", "understanding", "language"},
    "performance": {"coverage", "latency", "tools", "llm"},
    "leads": {"coverage", "lead_safety", "busy_recovery", "escalations"},
    "hosts": {"hosts", "properties"},
    "usage": {"usd_inr", "rows", "by_account", "totals", "daily"},
}


@pytest.fixture
def admin_headers(monkeypatch):
    monkeypatch.setattr(settings, "admin_jwt_secret", "test-admin-secret-0123456789abcdef")
    monkeypatch.setattr(settings, "admin_emails", ADMIN)

    async def _stub(service, refresh=False):
        return billing_clients._result(service, "not_configured")

    monkeypatch.setattr(billing_clients, "fetch_balance", _stub)
    token, _ = issue_admin_token(ADMIN)
    return {"Authorization": f"Bearer {token}"}


def _dump(name, payload):
    target = os.environ.get("ADMIN_PAYLOAD_DUMP_DIR")
    if target:
        with open(os.path.join(target, f"{name}.json"), "w") as f:
            json.dump(payload, f, indent=1, default=str)


@pytest.mark.parametrize("endpoint", sorted(EXPECTED_KEYS))
async def test_every_data_endpoint_answers_with_expected_shape(client, admin_headers, seeded, endpoint):  # noqa: F811
    resp = await client.get(f"/api/v1/admin/{endpoint}?{RANGE}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert EXPECTED_KEYS[endpoint] <= set(body), set(body) ^ EXPECTED_KEYS[endpoint]
    _dump(endpoint, body)


async def test_balances_and_settings_round_trip(client, admin_headers, seeded):  # noqa: F811
    balances = await client.get("/api/v1/admin/balances", headers=admin_headers)
    assert balances.status_code == 200
    accounts = {c["account"] for c in balances.json()}
    assert {"sarvam", "exotel", "groq", "resend", "twilio", "openrouter", "searchapi", "brightdata", "cloudinary", "neon"} <= accounts
    _dump("balances", balances.json())

    listed = await client.get("/api/v1/admin/settings/services", headers=admin_headers)
    assert listed.status_code == 200
    _dump("settings", listed.json())

    updated = await client.put(
        "/api/v1/admin/settings/services/exotel",
        json={"prepaid_amount": 500, "unit_prices": {"minutes": 0.75}},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    exotel = next(s for s in updated.json() if s["account"] == "exotel")
    assert exotel["prepaid_amount"] == 500
    assert exotel["unit_prices"]["minutes"] == 0.75
    assert exotel["updated_by"] == ADMIN

    card = next(c for c in (await client.get("/api/v1/admin/balances", headers=admin_headers)).json() if c["account"] == "exotel")
    assert card["status"] == "ok" and card["prepaid_amount"] == 500


async def test_range_defaults_and_validation(client, admin_headers):
    assert (await client.get("/api/v1/admin/overview", headers=admin_headers)).status_code == 200
    bad = await client.get("/api/v1/admin/overview?start_date=2031-03-11&end_date=2031-03-10", headers=admin_headers)
    assert bad.status_code == 422
    neg = await client.put("/api/v1/admin/settings/services/exotel", json={"unit_prices": {"minutes": -1}}, headers=admin_headers)
    assert neg.status_code == 422
    unknown = await client.put("/api/v1/admin/settings/services/nope", json={}, headers=admin_headers)
    assert unknown.status_code == 404
