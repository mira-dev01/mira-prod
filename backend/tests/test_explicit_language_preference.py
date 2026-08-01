"""Covers Phase 3.3 (documentation/agent-conversation-improvement.md):
update_lead's preferred_language argument sets
ConversationState.explicit_language_preference -- reproduces catalogue item
C5 (Phase 0.2) directly: a guest asked "can you speak Hindi?" mid-call and
the reply stayed in English. This is the tool-wrapper-level mechanism that
makes that fixable; the actual prompt-side recognition instruction lives in
GOLDEN_RULES (system_prompt.py), covered separately in test_system_prompt.py.
"""

from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self):
        self.result = None

    async def result_callback(self, result, **kwargs):
        self.result = result


async def test_update_lead_sets_explicit_language_preference_hindi(test_property, db_session, test_user):
    """Reproduces catalogue item C5 directly: guest asks 'can you speak
    Hindi?' -- the model is expected to call update_lead(preferred_language='hindi')
    per that argument's own docstring."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    await update_lead(_FakeFunctionCallParams(), preferred_language="hindi")

    assert state.explicit_language_preference == Language.HI_IN


async def test_update_lead_sets_explicit_language_preference_english(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    await update_lead(_FakeFunctionCallParams(), preferred_language="english")

    assert state.explicit_language_preference == Language.EN_IN


async def test_update_lead_unset_preferred_language_does_not_clobber_existing_preference(test_property, db_session, test_user):
    """A later update_lead call for an unrelated field (e.g. just num_guests)
    must not silently reset an explicit preference already set earlier."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    await update_lead(_FakeFunctionCallParams(), preferred_language="hindi")
    assert state.explicit_language_preference == Language.HI_IN

    await update_lead(_FakeFunctionCallParams(), num_guests=4)
    assert state.explicit_language_preference == Language.HI_IN


async def test_update_lead_unrecognized_preferred_language_is_ignored(test_property, db_session, test_user):
    """A value outside the constrained english/hindi vocabulary (e.g. the
    model hallucinating something else despite the docstring's constraint)
    fails open -- never crashes, never sets a garbage preference."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    await update_lead(_FakeFunctionCallParams(), preferred_language="klingon")

    assert state.explicit_language_preference is None


async def test_update_lead_preferred_language_never_written_to_lead_record(test_property, db_session, test_user):
    """Deliberately NOT persisted to the Lead DB row -- this only ever needs
    to live in ConversationState for the current call."""
    from sqlalchemy import select

    from app.models.lead import Lead

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    await update_lead(_FakeFunctionCallParams(), guest_name="Priya", preferred_language="hindi")

    lead = (await db_session.scalars(select(Lead))).first()
    assert lead is not None
    assert lead.guest_name == "Priya"
    assert not hasattr(lead, "preferred_language")
