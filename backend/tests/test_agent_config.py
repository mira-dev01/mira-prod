"""Agent rules (users.agent_config / properties.agent_config_overrides):
defaults, partial updates, validation, per-property payment overrides."""

import uuid

from app.database import AsyncSessionLocal
from app.models.property import Property
from app.models.user import User
from app.services import agent_config_service
from tests.conftest import auth_headers_for

URL = "/api/v1/agent-config"


async def _second_property(db, user):
    p = Property(user_id=user.id, name="Villa Two", city="Goa", base_price=3000, max_guests=2)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def test_new_host_gets_the_documented_defaults(client, auth_headers):
    body = (await client.get(URL, headers=auth_headers)).json()
    config = body["config"]
    assert config["collect"] == {
        "dates": "optional",
        "guests": "optional",
        "purpose": "optional",
        "location": "optional",
        "budget": "optional",
        "email": "off",
        "custom": [],
    }
    assert config["hot_lead"]["rules"] == []
    assert set(config["hot_lead"]["hot_on"]) == {"availability_confirmed", "pricing_requested", "negotiation_initiated"}
    assert config["booking"] == {"confirm_by": "host", "active_when": "confirmed"}
    assert config["payment"] == {
        "kind": "full",
        "split_percent": 30,
        "balance_due_days_before_checkin": 7,
        "hold_minutes": 60,
    }
    assert body["payment_overrides"] == {}


async def test_name_and_phone_are_not_configurable(client, auth_headers):
    config = (await client.get(URL, headers=auth_headers)).json()["config"]
    assert "name" not in config["collect"] and "phone" not in config["collect"]
    # And a client cannot add them: unknown fields inside a section are dropped, not stored.
    await client.put(URL, headers=auth_headers, json={"collect": {"name": "off", "phone": "off", "budget": "required"}})
    stored = (await client.get(URL, headers=auth_headers)).json()["config"]["collect"]
    assert "name" not in stored and "phone" not in stored and stored["budget"] == "required"


async def test_update_replaces_only_the_sections_sent(client, auth_headers):
    first = await client.put(
        URL,
        headers=auth_headers,
        json={"collect": {"budget": "required", "email": "optional"}, "payment": {"kind": "split", "hold_minutes": 90}},
    )
    assert first.status_code == 200, first.text
    second = await client.put(URL, headers=auth_headers, json={"hot_lead": {"hot_on": ["pricing_requested"]}})
    config = second.json()["config"]
    assert config["hot_lead"]["hot_on"] == ["pricing_requested"]
    # Earlier choices survive a later, unrelated update.
    assert config["collect"]["budget"] == "required" and config["collect"]["email"] == "optional"
    assert config["payment"]["kind"] == "split" and config["payment"]["hold_minutes"] == 90
    # A section is replaced as a whole: fields not sent fall back to their defaults.
    third = await client.put(URL, headers=auth_headers, json={"collect": {"dates": "required"}})
    assert third.json()["config"]["collect"]["budget"] == "optional"
    assert third.json()["config"]["collect"]["dates"] == "required"


async def test_invalid_values_are_rejected_and_nothing_is_stored(client, auth_headers):
    bad_bodies = [
        {"payment": {"split_percent": 0}},
        {"payment": {"split_percent": 100}},
        {"payment": {"hold_minutes": 4}},
        {"payment": {"hold_minutes": 1441}},
        {"payment": {"kind": "monthly"}},
        {"collect": {"budget": "maybe"}},
        {"booking": {"confirm_by": "robot"}},
        {"hot_lead": {"hot_on": ["nonsense"]}},
        {"unknown_section": {}},
    ]
    for body in bad_bodies:
        response = await client.put(URL, headers=auth_headers, json=body)
        assert response.status_code == 422, body
    config = (await client.get(URL, headers=auth_headers)).json()["config"]
    assert config["payment"]["kind"] == "full" and config["collect"]["budget"] == "optional"


async def test_property_payment_override_layers_over_the_portfolio(client, auth_headers, test_user, test_property, db_session):
    other = await _second_property(db_session, test_user)
    await client.put(URL, headers=auth_headers, json={"payment": {"kind": "split", "split_percent": 25, "hold_minutes": 90}})

    response = await client.put(
        f"{URL}/properties/{test_property.id}/payment",
        headers=auth_headers,
        json={"split_percent": 50, "hold_minutes": 30},
    )
    assert response.status_code == 200, response.text
    assert response.json()["payment_overrides"] == {
        str(test_property.id): {"kind": None, "split_percent": 50, "balance_due_days_before_checkin": None, "hold_minutes": 30}
    }

    async with AsyncSessionLocal() as db:
        user = await db.get(User, test_user.id)
        mine = await db.get(Property, test_property.id)
        theirs = await db.get(Property, other.id)
        overridden = agent_config_service.resolve_config(user, mine).payment
        followed = agent_config_service.resolve_config(user, theirs).payment
        no_property = agent_config_service.resolve_config(user, None).payment
    assert (overridden.kind, overridden.split_percent, overridden.hold_minutes) == ("split", 50, 30)  # kind inherited
    assert overridden.balance_due_days_before_checkin == 7  # inherited
    assert (followed.split_percent, followed.hold_minutes) == (25, 90)
    assert no_property.split_percent == 25

    cleared = await client.delete(f"{URL}/properties/{test_property.id}/payment", headers=auth_headers)
    assert cleared.json()["payment_overrides"] == {}


async def test_override_must_change_something_and_must_be_the_hosts_own(client, auth_headers, test_property, db_session):
    empty = await client.put(f"{URL}/properties/{test_property.id}/payment", headers=auth_headers, json={})
    assert empty.status_code == 422

    stranger = User(email=f"s-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(stranger)
    await db_session.commit()
    foreign = await client.put(
        f"{URL}/properties/{test_property.id}/payment", headers=auth_headers_for(stranger), json={"hold_minutes": 30}
    )
    assert foreign.status_code == 404
    foreign_delete = await client.delete(f"{URL}/properties/{test_property.id}/payment", headers=auth_headers_for(stranger))
    assert foreign_delete.status_code == 404


async def test_one_hosts_config_never_leaks_to_another(client, auth_headers, db_session):
    await client.put(URL, headers=auth_headers, json={"collect": {"budget": "required"}})
    stranger = User(email=f"t-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(stranger)
    await db_session.commit()
    theirs = (await client.get(URL, headers=auth_headers_for(stranger))).json()["config"]
    assert theirs["collect"]["budget"] == "optional"


async def test_a_corrupted_stored_value_degrades_to_defaults_instead_of_breaking(test_user):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, test_user.id)
        user.agent_config = {"collect": {"budget": "banana"}, "payment": {"hold_minutes": 90}, "legacy": 1}
        await db.commit()
        config = agent_config_service.get_config(user)
    assert config.collect.budget == "optional"  # bad section -> its defaults
    assert config.payment.hold_minutes == 90  # good section kept

    assert agent_config_service.parse_config(None).payment.hold_minutes == 60
    assert agent_config_service.parse_config("not a dict").collect.email == "off"  # type: ignore[arg-type]


async def test_busy_call_defaults_keep_todays_behaviour_and_are_validated(client, auth_headers):
    config = (await client.get(URL, headers=auth_headers)).json()["config"]
    assert config["busy_calls"] == {"mode": "callback_invite", "callback_wait_minutes": 5}

    saved = await client.put(URL, headers=auth_headers, json={"busy_calls": {"mode": "host_follows_up", "callback_wait_minutes": 10}})
    assert saved.json()["config"]["busy_calls"] == {"mode": "host_follows_up", "callback_wait_minutes": 10}
    # Other sections are untouched by it.
    assert saved.json()["config"]["payment"]["kind"] == "full"

    for bad in ({"mode": "agent_call"}, {"mode": "nonsense"}, {"callback_wait_minutes": 0}, {"callback_wait_minutes": 61}):
        assert (await client.put(URL, headers=auth_headers, json={"busy_calls": bad})).status_code == 422, bad


async def test_the_active_booking_rule_saves_and_rejects_unknown_values(client, auth_headers):
    saved = await client.put(URL, headers=auth_headers, json={"booking": {"confirm_by": "host", "active_when": "link_sent"}})
    assert saved.status_code == 200 and saved.json()["config"]["booking"]["active_when"] == "link_sent"
    assert (await client.get(URL, headers=auth_headers)).json()["config"]["booking"]["active_when"] == "link_sent"

    bad = await client.put(URL, headers=auth_headers, json={"booking": {"active_when": "whenever"}})
    assert bad.status_code == 422


async def test_sending_only_one_booking_field_keeps_the_other_as_saved(client, auth_headers):
    await client.put(URL, headers=auth_headers, json={"booking": {"confirm_by": "payment", "active_when": "link_sent"}})

    await client.put(URL, headers=auth_headers, json={"booking": {"confirm_by": "host"}})

    booking = (await client.get(URL, headers=auth_headers)).json()["config"]["booking"]
    assert booking == {"confirm_by": "host", "active_when": "link_sent"}
