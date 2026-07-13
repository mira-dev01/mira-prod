"""Covers Host Memory's discount-policy parsing + validation endpoints (see
memory-architecture-plan.md section 4). The actual LLM call is monkeypatched
-- these tests are about the endpoint/data-flow contract (pending_validation
by default, host approval required, ownership checks), not about LLM output
quality."""

import json

from app.services import discount_policy_service


async def test_parse_creates_pending_validation_rules(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {"trigger_type": "guest_requests", "discount_percent": 5},
                    {"trigger_type": "repeat_guest_same_host", "discount_percent": 8},
                ]
            }
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/host-discount-rules/parse",
        json={
            "discount_policy_text": (
                "If the guest doesn't ask, I keep the price as offered. If they ask for a discount, "
                "I offer 5%. Repeat guests across my properties get 8%."
            )
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["rules"]) == 2
    assert all(rule["status"] == "pending_validation" for rule in body["rules"])
    assert all(rule["source"] == "ai_parsed" for rule in body["rules"])
    trigger_types = {rule["trigger_type"] for rule in body["rules"]}
    assert trigger_types == {"guest_requests", "repeat_guest_same_host"}


async def test_parse_with_no_extractable_rules_returns_422(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps({"rules": []})

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/host-discount-rules/parse",
        json={"discount_policy_text": "I love hosting guests."},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_parse_with_malformed_llm_output_returns_502(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return "not json at all"

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/host-discount-rules/parse",
        json={"discount_policy_text": "5% off for everyone."},
        headers=auth_headers,
    )
    assert resp.status_code == 502


async def test_approve_rule_flips_status_and_marks_host_edited_if_changed(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps({"rules": [{"trigger_type": "guest_requests", "discount_percent": 5}]})

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/host-discount-rules/parse",
        json={"discount_policy_text": "5% off if asked."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/host-discount-rules/{rule_id}",
        json={"status": "approved", "discount_percent": 7},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    assert body["discount_percent"] == 7
    assert body["source"] == "host_edited"


async def test_cannot_approve_another_hosts_rule(client, auth_headers, db_session, monkeypatch):
    import uuid

    from app.auth.security import create_access_token, hash_password
    from app.models.user import User

    other_host = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("testpass123"),
        name="Other Host",
    )
    db_session.add(other_host)
    await db_session.commit()
    await db_session.refresh(other_host)
    other_headers = {"Authorization": f"Bearer {create_access_token(other_host.id)}"}

    async def _fake_call_groq(prompt):
        return json.dumps({"rules": [{"trigger_type": "guest_requests", "discount_percent": 5}]})

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/host-discount-rules/parse",
        json={"discount_policy_text": "5% off if asked."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    resp = await client.patch(
        f"/api/v1/host-discount-rules/{rule_id}", json={"status": "approved"}, headers=other_headers
    )
    assert resp.status_code == 404


async def test_list_only_returns_current_hosts_rules(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps({"rules": [{"trigger_type": "guest_requests", "discount_percent": 5}]})

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    await client.post(
        "/api/v1/host-discount-rules/parse",
        json={"discount_policy_text": "5% off if asked."},
        headers=auth_headers,
    )

    resp = await client.get("/api/v1/host-discount-rules", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
