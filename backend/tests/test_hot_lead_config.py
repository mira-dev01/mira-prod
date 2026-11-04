"""The Agent page's "what makes a lead hot" setting: it must change both the
deterministic floors and what the model is told."""

from datetime import date, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.models.user import User
from app.prompts.system_prompt import _hot_lead_section, build_lead_system_prompt
from app.schemas.agent_config import AgentConfig
from app.services import lead_service
from app.services import lead_temperature as temperature


def test_floor_without_a_host_choice_is_unchanged():
    assert temperature.engagement_floor("pricing_requested") == "hot"
    assert temperature.engagement_floor("availability_checked") == "warm"
    assert temperature.engagement_floor("negotiation_initiated", None) == "hot"


def test_a_signal_the_host_turned_off_floors_at_warm_not_hot():
    only_dates = ["availability_confirmed"]
    assert temperature.engagement_floor("availability_confirmed", only_dates) == "hot"
    assert temperature.engagement_floor("pricing_requested", only_dates) == "warm"
    assert temperature.engagement_floor("negotiation_initiated", only_dates) == "warm"
    # Checking availability was never a hot signal, so the host's choice does not touch it.
    assert temperature.engagement_floor("availability_checked", []) == "warm"
    assert temperature.engagement_floor("pricing_requested", []) == "warm"


async def _temperature_after(user_id, call_session_id, property_name, engagement):
    start = date.today() + timedelta(days=10)
    async with AsyncSessionLocal() as db:
        await lead_service.backfill_lead_from_engagement(
            db, user_id, call_session_id, property_name, start, start + timedelta(days=2), 2, engagement=engagement
        )
    async with AsyncSessionLocal() as db:
        return (await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))).lead_temperature


async def test_the_stored_lead_temperature_follows_the_hosts_choice(test_user, test_property, test_call_session):
    async with AsyncSessionLocal() as db:
        host = await db.get(User, test_user.id)
        host.agent_config = {"hot_lead": {"hot_on": ["availability_confirmed"]}}
        await db.commit()
    # Asking for a price no longer makes the lead hot for this host ...
    assert await _temperature_after(test_user.id, test_call_session.id, test_property.name, "pricing_requested") == "warm"
    # ... but confirmed dates still do, and a lead never cools back down.
    assert (
        await _temperature_after(test_user.id, test_call_session.id, test_property.name, "availability_confirmed")
        == "hot"
    )
    assert await _temperature_after(test_user.id, test_call_session.id, test_property.name, "pricing_requested") == "hot"


async def test_a_default_host_still_gets_hot_from_a_price_request(test_user, test_property, test_call_session):
    assert await _temperature_after(test_user.id, test_call_session.id, test_property.name, "pricing_requested") == "hot"


def test_prompt_wording_for_default_partial_and_empty_choices():
    assert _hot_lead_section(AgentConfig()) == ""

    partial = _hot_lead_section(AgentConfig.model_validate({"hot_lead": {"hot_on": ["pricing_requested"]}}))
    assert "only when the guest asks for a price" in partial and "stays warm" in partial
    assert "discount" not in partial

    none = _hot_lead_section(AgentConfig.model_validate({"hot_lead": {"hot_on": []}}))
    assert "do NOT make a lead hot" in none and "very_hot" in none


def test_prompt_carries_the_setting_only_when_it_was_changed(test_user, test_property):
    assert "What counts as a hot lead" not in build_lead_system_prompt(test_user, [test_property])
    test_user.agent_config = {"hot_lead": {"hot_on": ["availability_confirmed"]}}
    prompt = build_lead_system_prompt(test_user, [test_property])
    assert "What counts as a hot lead" in prompt and "confirmed available" in prompt
