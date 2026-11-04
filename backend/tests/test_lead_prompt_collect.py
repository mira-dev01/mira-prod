"""How the host's Collect settings reach the Lead Agent's instructions."""

from app.prompts.system_prompt import _collect_section, build_lead_system_prompt
from app.schemas.agent_config import AgentConfig

_HEADING = "What the host wants you to collect"


def test_default_settings_add_nothing_to_the_prompt():
    assert _collect_section(AgentConfig()) == ""


def test_must_have_nice_to_have_and_off_each_get_their_own_line():
    config = AgentConfig.model_validate(
        {"collect": {"dates": "required", "budget": "required", "guests": "off", "email": "optional"}}
    )
    text = _collect_section(config)
    assert "Must have: stay dates (or how many nights), budget." in text
    assert "Ask at most twice" in text and "never stops you saving the lead" in text
    assert "Nice to have: reason for the stay, preferred area, email address." in text
    assert "Do not ask for: number of guests." in text
    assert "email address even before" in text  # overrides the 'only when finalising' rule


def test_email_left_off_keeps_the_original_finalising_rule():
    config = AgentConfig.model_validate({"collect": {"budget": "required"}})
    text = _collect_section(config)
    assert "email" not in text.lower()


def test_prompt_includes_settings_only_for_a_configured_host(test_user, test_property):
    plain = build_lead_system_prompt(test_user, [test_property])
    assert _HEADING not in plain

    test_user.agent_config = {"collect": {"budget": "required"}}
    configured = build_lead_system_prompt(test_user, [test_property])
    assert _HEADING in configured and "Must have: budget." in configured
    # The settings come right after the workflow they amend.
    assert configured.index("Lead qualification workflow") < configured.index(_HEADING)


def test_workflow_pitches_facilities_from_the_locations_list_and_keeps_the_area(test_user, test_property):
    prompt = build_lead_system_prompt(test_user, [test_property])
    assert "Never offer a facility the Locations section does not list" in prompt
    assert "Stay in the area the guest named" in prompt
    assert 'ask "still\n   looking in <that area>?"' in prompt
