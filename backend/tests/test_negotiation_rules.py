"""Covers the unified negotiation/pricing-training endpoint (see
NegotiationRule's docstring for why this merges what used to be two
separate tables/endpoints -- host-wide discount triggers plus stay-pricing
rules). The actual LLM call is monkeypatched -- these tests are about the
endpoint/data-flow contract (pending_validation by default, host approval +
property-selection required for stay-pricing types, host-wide-by-default for
discount_* types, ownership checks), not about LLM output quality."""

import json
import uuid

from app.services import negotiation_policy_service


async def test_parse_creates_pending_validation_rules_across_both_rule_families(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 5, "label": None},
                    {"rule_type": "discount_repeat_guest", "condition": {}, "discount_percent": 8, "label": None},
                    {"rule_type": "minimum_stay_nights", "condition": {"weekend_min_nights": 2}, "discount_percent": None, "label": None},
                    {"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None},
                ]
            }
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={
            "policy_text": (
                "If a guest asks for a discount, I offer 5%. Repeat guests across my properties get 8%. "
                "Saturdays need a 2-night minimum. 10% off for stays over 5 nights."
            )
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["rules"]) == 4
    assert all(rule["status"] == "pending_validation" for rule in body["rules"])
    assert all(rule["source"] == "ai_parsed" for rule in body["rules"])
    assert all(rule["property_ids"] == [] for rule in body["rules"])
    rule_types = {rule["rule_type"] for rule in body["rules"]}
    assert rule_types == {"discount_guest_requests", "discount_repeat_guest", "minimum_stay_nights", "length_of_stay"}


async def test_parse_with_no_extractable_rules_returns_422(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps({"rules": []})

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "I love hosting guests."},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_parse_with_malformed_llm_output_returns_502(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return "not json at all"

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "5% off for everyone."},
        headers=auth_headers,
    )
    assert resp.status_code == 502


async def test_approve_discount_rule_flips_status_and_marks_host_edited_if_changed(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 5, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "5% off if asked."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}",
        json={"status": "approved", "discount_percent": 7},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    assert body["discount_percent"] == 7
    assert body["source"] == "host_edited"
    # Discount triggers are host-wide by definition -- approving one with no
    # property_ids selected still applies everywhere, unlike a stay-pricing rule.
    assert body["property_ids"] == []


async def test_patch_stages_round_trips_through_the_api(client, auth_headers, monkeypatch):
    """Phase 4D: stages is a new, optional field on NegotiationRuleUpdate/Out
    -- confirms it can be set via PATCH and reads back correctly, and that
    the mode="json" fix (app/api/v1/negotiation_rules.py) actually
    serializes the list[NegotiationStage] to plain dicts rather than
    failing to commit."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 5, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "5% off if asked."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    patch_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}",
        json={"status": "approved", "stages": [{"order": 0, "value": 4}, {"order": 1, "value": 9}]},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["stages"] == [{"order": 0, "value": 4.0}, {"order": 1, "value": 9.0}]
    # Self-review fix: a stages-only edit alongside approval must mark
    # source="host_edited", same as editing discount_percent already does --
    # stages is the staged equivalent of that same action-value field.
    assert body["source"] == "host_edited"

    list_resp = await client.get("/api/v1/negotiation-rules", headers=auth_headers)
    listed = next(r for r in list_resp.json() if r["id"] == rule_id)
    assert listed["stages"] == [{"order": 0, "value": 4.0}, {"order": 1, "value": 9.0}]


async def test_existing_rules_without_stages_report_stages_as_none(client, auth_headers, monkeypatch):
    """Backward compatibility: every rule authored before Phase 4D has no
    stages value -- must serialize as null, not error or default to []."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 5, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "5% off if asked."},
        headers=auth_headers,
    )
    assert parse_resp.json()["rules"][0]["stages"] is None


# ---------------------------------------------------------------------------
# Phase 4E: end-to-end progressive-policy authoring -- host text -> parser
# -> ONE staged NegotiationRule draft -> pending validation -> approval.
# ---------------------------------------------------------------------------


async def test_parse_progressive_policy_produces_one_staged_rule_not_three_flat_ones(client, auth_headers, monkeypatch):
    """The exact Step 2 problem this phase closes: a host describing a
    3-step pushback progression must land as ONE NegotiationRule with a
    3-entry stages list, never three independent discount_guest_requests
    rows the host would have to manually recognize as related."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {
                        "rule_type": "discount_guest_requests",
                        "condition": {},
                        "discount_percent": None,
                        "stages": [{"order": 0, "value": 2}, {"order": 1, "value": 4}, {"order": 2, "value": 6}],
                        "label": None,
                    }
                ]
            }
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "If guests ask for a discount, I can give them 2%, then 4%, then 6% if they keep negotiating."},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["rules"]) == 1  # ONE rule, not three
    assert body["rules"][0]["stages"] == [
        {"order": 0, "value": 2.0},
        {"order": 1, "value": 4.0},
        {"order": 2, "value": 6.0},
    ]
    assert body["rules"][0]["status"] == "pending_validation"


async def test_parse_flat_policy_still_produces_a_flat_rule(client, auth_headers, monkeypatch):
    """Example B from Step 15 -- "I don't negotiate" / a plain flat policy
    must not be affected by the staged-extraction addition."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "discount_no_ask", "condition": {}, "discount_percent": 0, "stages": None, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "I don't negotiate."},
        headers=auth_headers,
    )
    body = resp.json()
    assert body["rules"][0]["stages"] is None
    assert body["rules"][0]["discount_percent"] == 0.0


async def test_staged_rule_remains_inactive_while_pending(client, auth_headers, db_session, test_user, monkeypatch):
    """Step 18 item 15 / Step 11: no unapproved staged rule may affect
    runtime -- confirmed directly against pricing_engine, not just the API
    surface, since that's the actual enforcement point."""
    from datetime import date, timedelta

    from app.models.negotiation_rule import NegotiationRule
    from app.models.property import Property
    from app.services.pricing_engine import negotiate_rate

    prop = Property(user_id=test_user.id, name="Pending Test Villa", city="Goa", exophone="+918000099920", base_price=4000, max_guests=4)
    db_session.add(prop)
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 50}],  # huge value -- a leak would be obvious
            status="pending_validation",  # NOT approved
        )
    )
    await db_session.commit()
    await db_session.refresh(prop)

    today = date.today()
    result = await negotiate_rate(db_session, prop, today + timedelta(days=10), today + timedelta(days=12), guest_offer=None, host_id=test_user.id)
    assert result.is_staged is False  # pending rule never reached the runtime evaluator


async def test_approval_activates_the_complete_staged_policy_atomically(client, auth_headers, monkeypatch):
    """Step 11/18 item 16: approving a staged rule approves the WHOLE
    ordered sequence in one action -- there's no way to approve "stage 1
    only" since stages live on a single NegotiationRule row, not
    independent rows. Confirms the approved rule's full stage list is
    intact, not partially applied."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {
                        "rule_type": "discount_guest_requests",
                        "condition": {},
                        "discount_percent": None,
                        "stages": [{"order": 0, "value": 3}, {"order": 1, "value": 6}, {"order": 2, "value": 9}, {"order": 3, "value": 12}],
                        "label": None,
                    }
                ]
            }
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "3%, then 6%, then 9%, then 12% if they really push."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}", json={"status": "approved"}, headers=auth_headers
    )
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    # All 4 stages present -- not just the first, not a subset.
    assert len(body["stages"]) == 4
    assert body["stages"] == [
        {"order": 0, "value": 3.0},
        {"order": 1, "value": 6.0},
        {"order": 2, "value": 9.0},
        {"order": 3, "value": 12.0},
    ]


async def test_edit_preserves_stage_structure(client, auth_headers, test_property, monkeypatch):
    """Step 18 item 17: editing a staged rule's values (via PATCH stages,
    the same endpoint the frontend's staged-edit form now uses) must
    preserve ordering and arbitrary stage count -- not collapse to a flat
    value or silently drop stages. Also sends property_ids alongside
    stages, matching what the frontend's edit form now does for a
    property-scoped "custom" rule (see the self-review fix below)."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {
                        "rule_type": "custom",
                        "condition": {},
                        "discount_percent": None,
                        "stages": [{"order": 0, "value": 5}, {"order": 1, "value": 15}],
                        "label": "Villa A ladder",
                    }
                ]
            }
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse", json={"policy_text": "For Villa A, 5% then 15%."}, headers=auth_headers
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    # Host edits the values (not the count) before approving.
    edit_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}",
        json={
            "stages": [{"order": 0, "value": 7}, {"order": 1, "value": 18}],
            "property_ids": [str(test_property.id)],
            "status": "approved",
        },
        headers=auth_headers,
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["stages"] == [{"order": 0, "value": 7.0}, {"order": 1, "value": 18.0}]
    assert edit_resp.json()["property_ids"] == [str(test_property.id)]


async def test_approving_a_staged_custom_rule_without_property_ids_is_a_silent_no_op(client, auth_headers, monkeypatch):
    """Self-review regression: a "custom" (property-scoped) staged rule
    approved with NO property_ids is accepted by the API (no server-side
    validation currently prevents it -- see the route's own comment on
    property_ids only being checked "if updates.get('property_ids')" is
    truthy) but has ZERO runtime effect, since _approved_property_pricing_rules
    filters by property_ids membership and an empty list matches no
    property. This test documents the exact trap the frontend's edit form
    previously fell into (calling "Save and approve" on a staged custom
    rule without ever collecting property_ids) -- the frontend fix
    ensures this codepath is never hit from the UI, but the backend
    itself still allows it if called directly, which this test makes
    explicit rather than leaving as an undocumented footgun."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {
                        "rule_type": "custom",
                        "condition": {},
                        "discount_percent": None,
                        "stages": [{"order": 0, "value": 5}, {"order": 1, "value": 15}],
                        "label": "Villa A ladder",
                    }
                ]
            }
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse", json={"policy_text": "For Villa A, 5% then 15%."}, headers=auth_headers
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    # Approve WITHOUT property_ids -- the API accepts this (confirmed, not
    # asserted as desirable), which is exactly why the frontend must always
    # send property_ids for a "custom" rule (see ai-training-section.tsx's
    # handleSaveEdit).
    approve_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}", json={"status": "approved"}, headers=auth_headers
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["property_ids"] == []
    assert approve_resp.json()["status"] == "approved"


async def test_parsed_staged_policy_reaches_phase_4d_runtime_correctly(client, auth_headers, db_session, test_user, monkeypatch):
    """Step 18 item 20: the full authoring pipeline (parse -> approve) must
    produce a rule that Phase 4D's negotiate_rate resolves exactly as a
    directly-constructed staged NegotiationRule would -- no second
    representation, no second evaluator."""
    from datetime import date, timedelta

    from app.models.property import Property
    from app.services.pricing_engine import negotiate_rate

    prop = Property(user_id=test_user.id, name="Runtime Test Villa", city="Goa", exophone="+918000099921", base_price=4000, max_guests=4)
    db_session.add(prop)
    await db_session.commit()
    await db_session.refresh(prop)

    async def _fake_call_groq(prompt):
        return json.dumps(
            {
                "rules": [
                    {
                        "rule_type": "discount_guest_requests",
                        "condition": {},
                        "discount_percent": None,
                        "stages": [{"order": 0, "value": 4}, {"order": 1, "value": 9}],
                        "label": None,
                    }
                ]
            }
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse", json={"policy_text": "4% first, 9% if they push again."}, headers=auth_headers
    )
    rule_id = parse_resp.json()["rules"][0]["id"]
    await client.patch(f"/api/v1/negotiation-rules/{rule_id}", json={"status": "approved"}, headers=auth_headers)

    today = date.today()
    check_in, check_out = today + timedelta(days=10), today + timedelta(days=12)
    result = await negotiate_rate(db_session, prop, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.is_staged is True
    assert result.stage_count == 2
    expected_floor = round(result.asking_price * (1 - 4 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_approve_stay_pricing_rule_with_property_selection(client, auth_headers, test_property, monkeypatch):
    """Selecting which properties a stay-pricing rule applies to and
    approving it in the same request is the normal flow (the AI never picks
    properties itself) -- source stays "ai_parsed" since only the rule's own
    substance (rule_type/condition/discount_percent/label) being changed
    counts as a host edit."""
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}",
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

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    approve_resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}",
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

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}",
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
            {"rules": [{"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 5, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "5% off if asked."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    resp = await client.patch(
        f"/api/v1/negotiation-rules/{rule_id}", json={"status": "approved"}, headers=other_headers
    )
    assert resp.status_code == 404


async def test_list_only_returns_current_hosts_rules(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "discount_guest_requests", "condition": {}, "discount_percent": 5, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "5% off if asked."},
        headers=auth_headers,
    )

    resp = await client.get("/api/v1/negotiation-rules", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_delete_rule(client, auth_headers, monkeypatch):
    async def _fake_call_groq(prompt):
        return json.dumps(
            {"rules": [{"rule_type": "length_of_stay", "condition": {"min_nights": 5}, "discount_percent": 10, "label": None}]}
        )

    monkeypatch.setattr(negotiation_policy_service, "_call_groq", _fake_call_groq)
    monkeypatch.setattr(negotiation_policy_service.settings, "groq_api_key", "test-key")

    parse_resp = await client.post(
        "/api/v1/negotiation-rules/parse",
        json={"policy_text": "10% off for stays over 5 nights."},
        headers=auth_headers,
    )
    rule_id = parse_resp.json()["rules"][0]["id"]

    delete_resp = await client.delete(f"/api/v1/negotiation-rules/{rule_id}", headers=auth_headers)
    assert delete_resp.status_code == 204

    resp = await client.get("/api/v1/negotiation-rules", headers=auth_headers)
    assert resp.json() == []
