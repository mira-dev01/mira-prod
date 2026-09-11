import pytest
from pydantic import ValidationError

from app.schemas.user import UserUpdate


def test_agent_escalation_phrase_rejects_loop_in_the_host_variants():
    # Regression: nothing validated agent_escalation_phrase at save time, so
    # a host could set it to the exact phrase GOLDEN_RULES bans the voice
    # agent from ever saying ("let me loop in the host") -- confirmed live,
    # the model then said it verbatim on a real call. Reject at the API
    # boundary instead of only papering over it in the prompt builder.
    for phrase in [
        "One sec, let me loop in the host directly!",
        "Let me loop the host in.",
        "host, let me loop you in",
    ]:
        with pytest.raises(ValidationError):
            UserUpdate(agent_escalation_phrase=phrase)


def test_agent_escalation_phrase_allows_safe_custom_wording():
    update = UserUpdate(agent_escalation_phrase="One moment, let me get my colleague Raj.")
    assert update.agent_escalation_phrase == "One moment, let me get my colleague Raj."


def test_agent_escalation_phrase_allows_none():
    update = UserUpdate(agent_escalation_phrase=None)
    assert update.agent_escalation_phrase is None


def test_agent_handoff_phrase_rejects_loop_in_the_host_variants():
    for phrase in [
        "Hold on, let me loop in the host.",
        "host, let me loop you in now",
    ]:
        with pytest.raises(ValidationError):
            UserUpdate(agent_handoff_phrase=phrase)


def test_agent_handoff_phrase_allows_safe_custom_wording():
    update = UserUpdate(agent_handoff_phrase="One sec -- connecting you to the owner.")
    assert update.agent_handoff_phrase == "One sec -- connecting you to the owner."


# --- Account-global host call hours ----------------------------------------


def test_host_call_hours_accepts_a_valid_window():
    update = UserUpdate(
        host_call_hours_enabled=True,
        host_call_hours_start="09:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    assert update.host_call_hours_enabled is True
    assert update.host_call_hours_start == "09:00"
    assert update.host_call_hours_end == "17:00"


def test_host_call_hours_accepts_an_overnight_window():
    update = UserUpdate(
        host_call_hours_enabled=True,
        host_call_hours_start="22:00",
        host_call_hours_end="06:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    assert update.host_call_hours_start == "22:00"


def test_host_call_hours_rejects_a_malformed_time():
    with pytest.raises(ValidationError):
        UserUpdate(host_call_hours_start="9am")
    with pytest.raises(ValidationError):
        UserUpdate(host_call_hours_end="25:00")


def test_host_call_hours_rejects_an_invalid_iana_timezone():
    with pytest.raises(ValidationError):
        UserUpdate(host_call_hours_timezone="Not/A_Real_Zone")


def test_host_call_hours_accepts_other_iana_timezones():
    assert UserUpdate(host_call_hours_timezone="America/New_York").host_call_hours_timezone == "America/New_York"


def test_enabling_the_window_requires_both_bounds_in_the_same_request():
    with pytest.raises(ValidationError):
        UserUpdate(host_call_hours_enabled=True)
    with pytest.raises(ValidationError):
        UserUpdate(host_call_hours_enabled=True, host_call_hours_start="09:00")
    with pytest.raises(ValidationError):
        UserUpdate(host_call_hours_enabled=True, host_call_hours_end="17:00")


def test_disabling_the_window_does_not_require_bounds():
    update = UserUpdate(host_call_hours_enabled=False)
    assert update.host_call_hours_enabled is False


def test_touching_an_unrelated_field_never_trips_the_window_cross_check():
    update = UserUpdate(name="New Name")
    assert update.host_call_hours_enabled is None


def test_blank_strings_normalize_to_none():
    update = UserUpdate(host_call_hours_start="", host_call_hours_end="", host_call_hours_timezone="")
    assert update.host_call_hours_start is None
    assert update.host_call_hours_end is None
    assert update.host_call_hours_timezone is None


def test_agent_language_policy_accepts_valid_values():
    """Phase 3.3 (documentation/agent-conversation-improvement.md)."""
    assert UserUpdate(agent_language_policy="hindi_first").agent_language_policy == "hindi_first"
    assert UserUpdate(agent_language_policy="english_first").agent_language_policy == "english_first"


def test_agent_language_policy_allows_none():
    update = UserUpdate(agent_language_policy=None)
    assert update.agent_language_policy is None


def test_agent_language_policy_rejects_invalid_value():
    with pytest.raises(ValidationError):
        UserUpdate(agent_language_policy="klingon_first")
