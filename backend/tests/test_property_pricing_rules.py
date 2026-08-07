"""Covers Phase 6 (Negotiation engine)'s pricing-policy parsing + validation
endpoints -- same contract as test_host_discount_rules.py (pending_validation
by default, host approval + property-selection required, ownership checks).
The actual LLM call is monkeypatched via discount_policy_service._call_groq
(pricing_policy_service.parse_pricing_policy_text reuses
discount_policy_service.call_configured_llm_for_json, so this is the same
monkeypatch point as the discount-policy tests, not a duplicate mechanism).
"""

import json
import uuid

from app.services import discount_policy_service


async def test_parse_creates_pending_validation_rules_with_no_properties_selected(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {"rule_type": "minimum_stay_nights", "condition": {"weekend_min_nights": 2}, "discount_percent": None, "label": None},
                    {"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None},
                ]
            }
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "Saturdays need a 2-night minimum. 10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["rules"]) == 2
    assert all(rule["status"] == "pending_validation" for rule in body["rules"])
    assert all(rule["source"] == "ai_parsed" for rule in body["rules"])
    assert all(rule["property_ids"] == [] for rule in body["rules"])
    rule_types = {rule["rule_type"] for rule in body["rules"]}
    assert rule_types == {"minimum_stay_nights", "length_of_stay"}


async def test_parse_with_no_extractable_rules_returns_422(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps({"rules": []})

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "I love hosting guests."},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_parse_with_malformed_llm_output_returns_502(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return "not json at all"

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "some pricing policy"},
        headers=auth_headers,
    )
    assert resp.status_code == 502


async def test_approve_rule_with_property_selection(client, auth_headers, test_property, monkeypatch):
    """Selecting which properties a rule applies to and approving it in the
    same request is the normal flow (the AI never picks properties itself)
    -- source stays "ai_parsed" since only the rule's own substance
    (rule_type/condition/discount_percent/label) being changed counts as a
    host edit, same distinction HostDiscountRule already makes."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/property-pricing-rules/{rule_id}",
        json={"status": "approved", "property_ids": [str(test_property.id)]},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    assert body["property_ids"] == [str(test_property.id)]
    assert body["source"] == "ai_parsed"


async def test_approve_rule_with_edited_discount_marks_host_edited(client, auth_headers, test_property, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/property-pricing-rules/{rule_id}",
        json={"status": "approved", "discount_percent": 15, "property_ids": [str(test_property.id)]},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["discount_percent"] == 15
    assert body["source"] == "host_edited"


async def test_approve_with_property_not_owned_by_host_returns_404(client, auth_headers, db_session, monkeypatch):
    from app.models.property import Property
    from app.models.user import User

    other_host = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", name="Other Host")
    db_session.add(other_host)
    await db_session.commit()
    await db_session.refresh(other_host)

    other_property = Property(
        user_id=other_host.id, name="Other Villa", base_price=3000, max_guests=2, exophone=f"+9180{uuid.uuid4().int % 10**8:08d}"
    )
    db_session.add(other_property)
    await db_session.commit()
    await db_session.refresh(other_property)

    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    resp = await client.patch(
        f"/api/v1/property-pricing-rules/{rule_id}",
        json={"status": "approved", "property_ids": [str(other_property.id)]},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_cannot_approve_another_hosts_rule(client, auth_headers, db_session, monkeypatch):
    from app.models.user import User

    other_host = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", name="Other Host")
    db_session.add(other_host)
    await db_session.commit()
    await db_session.refresh(other_host)
    other_headers = {"Authorization": f"Bearer {other_host.id}"}

    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    resp = await client.patch(
        f"/api/v1/property-pricing-rules/{rule_id}", json={"status": "approved"}, headers=other_headers
    )
    assert resp.status_code == 404


async def test_list_only_returns_current_hosts_rules(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )

    resp = await client.get("/api/v1/property-pricing-rules", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_delete_rule(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(discount_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(discount_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/property-pricing-rules/parse",
        json={"pricing_policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    delete_resp = await client.delete(f"/api/v1/property-pricing-rules/{rule_id}", headers=auth_headers)
    assert delete_resp.status_code == 204

    resp = await client.get("/api/v1/property-pricing-rules", headers=auth_headers)
    assert resp.json() == []
