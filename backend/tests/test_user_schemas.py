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
