import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.host_capability import HostCapability
from app.models.negotiation_rule import NegotiationRule
from app.models.property import Property
from app.models.technician import Technician
from app.models.user import User
from app.schemas.tool import DispatchTechnicianArgs
from app.services import capability_registry, capability_service, tool_handlers
from app.services.capability_registry import CAPABILITIES, CAPABILITIES_BY_ID, GROUPS_BY_ID
from tests.conftest import auth_headers_for


def _by_id(body: dict) -> dict[str, dict]:
    return {c["id"]: c for c in body["capabilities"]}


async def _make_user(db_session, **fields) -> User:
    user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
        **fields,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ── Registry integrity ─────────────────────────────────────────────────


def test_registry_ids_unique_and_references_valid():
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids))
    for cap in CAPABILITIES:
        assert cap.group in GROUPS_BY_ID
        for dep in (*cap.hard_dependencies, *cap.soft_dependencies):
            assert dep in CAPABILITIES_BY_ID, f"{cap.id} depends on unknown {dep}"
        for req in cap.requirements:
            assert req.id in capability_service._REQUIREMENT_CHECKS
        for integration in cap.integrations:
            assert integration.id in capability_service._INTEGRATIONS
        if cap.activation == "bound":
            assert cap.bound_user_field and hasattr(User, cap.bound_user_field)
        if cap.live_requirement:
            assert cap.live_requirement in capability_service._REQUIREMENT_CHECKS
            assert cap.live_block_reason
        if cap.activation == "core":
            assert cap.default_enabled and cap.enforcement == "always_on"


def test_registry_has_all_six_groups_and_no_dependency_cycles():
    assert set(GROUPS_BY_ID) == {
        "guest_calls",
        "bookings_pricing",
        "guest_support",
        "leads_crm",
        "properties_knowledge",
        "analytics",
    }

    def visit(cap_id: str, stack: tuple[str, ...]) -> None:
        assert cap_id not in stack, f"cycle: {stack + (cap_id,)}"
        for dep in CAPABILITIES_BY_ID[cap_id].hard_dependencies:
            visit(dep, stack + (cap_id,))

    for cap in CAPABILITIES:
        visit(cap.id, ())


def test_safety_net_capabilities_are_core():
    # Invariant: a genuine guest opportunity must not silently disappear --
    # none of these may become host-disableable.
    for cap_id in ("busy_call_recovery", "host_handoff", "lead_capture", "knowledge_faq", "guest_memory"):
        assert CAPABILITIES_BY_ID[cap_id].activation == "core"


# ── API: catalogue + host state ────────────────────────────────────────


async def test_catalog_requires_auth(client):
    resp = await client.get("/api/v1/capabilities/catalog")
    assert resp.status_code == 401


async def test_catalog_lists_registry(client, auth_headers):
    resp = await client.get("/api/v1/capabilities/catalog", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["groups"]) == 6
    assert {c["id"] for c in body["capabilities"]} == set(CAPABILITIES_BY_ID)


async def test_existing_host_defaults_match_pre_capability_behavior(client, auth_headers):
    resp = await client.get("/api/v1/capabilities", headers=auth_headers)
    assert resp.status_code == 200
    caps = _by_id(resp.json())
    # Everything that worked before capabilities existed is still on.
    for cap_id in ("guest_support_agent", "lead_agent", "negotiation", "calendar_sync", "technician_dispatch"):
        assert caps[cap_id]["enabled"] is True, cap_id
    # Live Airbnb pricing was always opt-in per property.
    assert caps["live_airbnb_pricing"]["enabled"] is False
    assert caps["guest_support_agent"]["can_disable"] is False


async def test_readiness_reflects_real_data(client, auth_headers, db_session, test_user):
    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["guest_support_agent"]["state"] == "needs_setup"
    assert caps["pricing_quotes"]["state"] == "needs_setup"
    assert caps["technician_dispatch"]["state"] == "needs_setup"

    property_ = Property(user_id=test_user.id, name="Villa", city="Goa", exophone="+918000000001", base_price=5000)
    db_session.add(property_)
    await db_session.commit()
    db_session.add(Technician(property_id=property_.id, name="Raj", specialty="general", phone="+919000000000"))
    await db_session.commit()

    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["guest_support_agent"]["state"] == "ready"
    assert caps["pricing_quotes"]["state"] == "ready"
    assert caps["technician_dispatch"]["state"] == "ready"
    # Soft requirement unmet -> still ready, but surfaced.
    handoff = caps["host_handoff"]
    assert handoff["state"] == "ready"
    assert any(r["id"] == "host_phone" and not r["met"] for r in handoff["requirements"])


async def test_unknown_capability_404(client, auth_headers):
    resp = await client.patch("/api/v1/capabilities/not_a_thing", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 404


async def test_core_capability_cannot_be_disabled(client, auth_headers):
    resp = await client.patch("/api/v1/capabilities/busy_call_recovery", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 409


async def test_disable_and_reenable_preference_persists(client, auth_headers, db_session, test_user):
    resp = await client.patch(
        "/api/v1/capabilities/technician_dispatch", json={"enabled": False}, headers=auth_headers
    )
    assert resp.status_code == 200
    caps = _by_id(resp.json())
    assert caps["technician_dispatch"]["enabled"] is False
    assert caps["technician_dispatch"]["state"] == "disabled"

    row = await db_session.scalar(
        select(HostCapability).where(
            HostCapability.user_id == test_user.id, HostCapability.capability_id == "technician_dispatch"
        )
    )
    assert row is not None and row.enabled is False and row.disabled_at is not None

    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["technician_dispatch"]["enabled"] is False

    resp = await client.patch("/api/v1/capabilities/technician_dispatch", json={"enabled": True}, headers=auth_headers)
    assert _by_id(resp.json())["technician_dispatch"]["enabled"] is True


async def test_negotiation_is_bound_to_existing_user_field(client, auth_headers, db_session, test_user):
    resp = await client.patch("/api/v1/capabilities/negotiation", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 200
    await db_session.refresh(test_user)
    assert test_user.negotiation_allowed is False

    # The existing field stays the source of truth: flipping it through the
    # pre-existing PATCH /auth/me is reflected immediately.
    await client.patch("/api/v1/auth/me", json={"negotiation_allowed": True}, headers=auth_headers)
    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["negotiation"]["enabled"] is True


async def test_negotiation_policy_soft_requirement(client, auth_headers, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="max_discount", status="approved", raw_source_text="10% max")
    )
    await db_session.commit()
    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert next(r for r in caps["negotiation"]["requirements"] if r["id"] == "negotiation_policy")["met"] is True


async def test_lead_agent_cannot_be_disabled_while_number_live(client, auth_headers, db_session, test_user):
    test_user.lead_exophone = "+918011112222"
    await db_session.commit()

    resp = await client.patch("/api/v1/capabilities/lead_agent", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 409
    assert "Guest Call Number" in resp.json()["detail"]

    # A stored "off" preference never hides a number that's still live.
    db_session.add(HostCapability(user_id=test_user.id, capability_id="lead_agent", enabled=False))
    await db_session.commit()
    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["lead_agent"]["enabled"] is True
    assert caps["lead_agent"]["live_in_backend"] is True


async def test_lead_agent_can_be_disabled_without_number(client, auth_headers):
    resp = await client.patch("/api/v1/capabilities/lead_agent", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 200
    assert _by_id(resp.json())["lead_agent"]["enabled"] is False


async def test_live_pricing_unavailable_without_integration(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "searchapi_api_key", None)
    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["live_airbnb_pricing"]["state"] == "unavailable"
    resp = await client.patch("/api/v1/capabilities/live_airbnb_pricing", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 409


async def test_live_pricing_disable_blocked_while_property_uses_it(
    client, auth_headers, db_session, test_user, monkeypatch
):
    monkeypatch.setattr(settings, "searchapi_api_key", "test-key")
    db_session.add(
        Property(
            user_id=test_user.id, name="Live", base_price=0, exact_airbnb_pricing=True, airbnb_listing_id="123"
        )
    )
    await db_session.commit()

    caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers)).json())
    assert caps["live_airbnb_pricing"]["enabled"] is True
    assert caps["live_airbnb_pricing"]["state"] == "ready"
    resp = await client.patch(
        "/api/v1/capabilities/live_airbnb_pricing", json={"enabled": False}, headers=auth_headers
    )
    assert resp.status_code == 409


async def test_hard_dependency_blocks_enable_and_dependent_blocks_disable(
    client, auth_headers, db_session, test_user, monkeypatch
):
    # Synthetic registry: no two real preference capabilities depend on
    # each other today, so exercise the generic mechanism directly.
    parent = capability_registry.Capability(
        id="parent_pref", name="Parent", description="", benefit="", group="guest_support",
        activation="preference", enforcement="advisory", default_enabled=False, on_effect="on", off_effect="off",
    )
    child = capability_registry.Capability(
        id="child_pref", name="Child", description="", benefit="", group="guest_support",
        activation="preference", enforcement="advisory", default_enabled=False, on_effect="on", off_effect="off",
        hard_dependencies=("parent_pref",),
    )
    caps = (*CAPABILITIES, parent, child)
    by_id = {c.id: c for c in caps}
    monkeypatch.setattr(capability_service, "CAPABILITIES", caps)
    monkeypatch.setattr(capability_service, "get_capability", by_id.get)
    monkeypatch.setattr(
        capability_service, "dependents_of", lambda cid: tuple(c for c in caps if cid in c.hard_dependencies)
    )

    resp = await client.patch("/api/v1/capabilities/child_pref", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 409
    assert "Parent" in resp.json()["detail"]

    assert (await client.patch("/api/v1/capabilities/parent_pref", json={"enabled": True}, headers=auth_headers)).status_code == 200
    assert (await client.patch("/api/v1/capabilities/child_pref", json={"enabled": True}, headers=auth_headers)).status_code == 200

    resp = await client.patch("/api/v1/capabilities/parent_pref", json={"enabled": False}, headers=auth_headers)
    assert resp.status_code == 409
    assert "Child" in resp.json()["detail"]


async def test_setup_state_and_config_validation(client, auth_headers):
    resp = await client.patch(
        "/api/v1/capabilities/technician_dispatch", json={"setup_state": "deferred"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert _by_id(resp.json())["technician_dispatch"]["setup_state"] == "deferred"

    resp = await client.patch(
        "/api/v1/capabilities/technician_dispatch", json={"config": {"made_up": True}}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_capability_state_is_host_isolated(client, auth_headers, db_session, test_user):
    other = await _make_user(db_session)
    await client.patch("/api/v1/capabilities/technician_dispatch", json={"enabled": False}, headers=auth_headers)

    other_caps = _by_id((await client.get("/api/v1/capabilities", headers=auth_headers_for(other))).json())
    assert other_caps["technician_dispatch"]["enabled"] is True

    rows = (await db_session.scalars(select(HostCapability).where(HostCapability.user_id == other.id))).all()
    assert rows == []


# ── Enforcement: technician dispatch ───────────────────────────────────


async def test_dispatch_disabled_flags_host_without_naming_technician(test_property, db_session):
    from app.services.notification_service import list_notifications

    db_session.add(
        Technician(property_id=test_property.id, name="Plumber Joe", specialty="plumbing", phone="+911234567890")
    )
    db_session.add(HostCapability(user_id=test_property.user_id, capability_id="technician_dispatch", enabled=False))
    await db_session.commit()

    args = DispatchTechnicianArgs(property_id=str(test_property.id), issue_type="plumbing", urgency="high")
    result = await tool_handlers.handle_dispatch_technician(db_session, args, call_session_id=None)
    assert "Plumber Joe" not in result
    assert "flagged" in result.lower()

    notifications = await list_notifications(db_session)
    assert any("Technician dispatch is turned off" in n.message for n in notifications)
    assert not any("Plumber Joe" in n.message for n in notifications)


async def test_is_capability_enabled_fails_open(monkeypatch):
    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(capability_service, "AsyncSessionLocal", _Boom())
    assert await capability_service.is_capability_enabled(uuid.uuid4(), "technician_dispatch") is True


@pytest.mark.parametrize("cap_id", ["guest_support_agent", "unknown_cap"])
async def test_is_capability_enabled_core_and_unknown_always_true(cap_id):
    assert await capability_service.is_capability_enabled(uuid.uuid4(), cap_id) is True
