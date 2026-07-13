"""Covers the property-lock bug fix in app/voice/conversation_state.py +
app/voice/tools.py (see memory-architecture-plan.md section 2): once a Lead
Agent (portfolio-wide) call names a specific property via a tool call that
already requires one (check_calendar/get_pricing/negotiate_rate), search_faq
must scope to that property even if the LLM never explicitly supplies
faq_property_id -- and recommend_properties must refuse to re-browse the
whole portfolio with no new criteria once a property is locked.
"""

from datetime import date, timedelta

from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.voice.conversation_state import ConversationState
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self):
        self.result = None

    async def result_callback(self, result):
        self.result = result


async def _second_property(db_session, test_user, name="Ocean View"):
    import uuid

    property_ = Property(
        user_id=test_user.id,
        name=name,
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        base_price=6000,
        max_guests=6,
    )
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)
    return property_


async def test_search_faq_locks_to_property_named_via_check_calendar(test_property, db_session, test_user):
    """Reproduces the reported bug: Lead Agent call (property_id=None), guest
    names a property via check_calendar, then asks a follow-up FAQ question
    without the LLM passing faq_property_id -- must still scope to the
    property just named, not search the whole portfolio."""
    other_property = await _second_property(db_session, test_user, name="Palm Retreat")
    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            property_id=test_property.id,
            question="Does it have a private pool?",
            answer="Yes, Test Villa has a private pool.",
            status="verified",
        )
    )
    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            property_id=other_property.id,
            question="Does it have a private pool?",
            answer="No, Palm Retreat does not have a private pool.",
            status="verified",
        )
    )
    await db_session.commit()

    state = ConversationState()
    # Lead Agent mode: property_id=None at closure level, exactly like a real
    # portfolio-wide call before any property has been chosen.
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    check_calendar = next(t for t in tools if t.__name__ == "check_calendar")
    search_faq = next(t for t in tools if t.__name__ == "search_faq")

    today = date.today()
    params = _FakeFunctionCallParams()
    await check_calendar(
        params,
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=10)).isoformat(),
        check_out=(today + timedelta(days=12)).isoformat(),
    )
    assert state.selected_property_id == str(test_property.id)

    # The LLM asks about "does it have a private pool" WITHOUT supplying
    # faq_property_id -- this is exactly the failure mode reported: absent
    # the state-based fallback, this would run portfolio-wide (property_id
    # falls back to None) and could return either property's answer.
    faq_params = _FakeFunctionCallParams()
    await search_faq(faq_params, query="does it have a private pool")
    assert "Test Villa has a private pool" in faq_params.result
    assert "Palm Retreat" not in faq_params.result


async def test_property_switch_updates_lock(test_property, db_session, test_user):
    """'I'd like to look at Ocean View instead' -- the next tool call naming
    a different property must overwrite the lock, not keep the old one."""
    ocean_view = await _second_property(db_session, test_user, name="Ocean View")

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")

    today = date.today()
    params = _FakeFunctionCallParams()
    await get_pricing(
        params,
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=1)).isoformat(),
        check_out=(today + timedelta(days=3)).isoformat(),
        num_guests=2,
    )
    assert state.selected_property_id == str(test_property.id)

    switch_params = _FakeFunctionCallParams()
    await get_pricing(
        switch_params,
        property_id=str(ocean_view.id),
        check_in=(today + timedelta(days=1)).isoformat(),
        check_out=(today + timedelta(days=3)).isoformat(),
        num_guests=2,
    )
    assert state.selected_property_id == str(ocean_view.id)


async def test_recommend_properties_refuses_redundant_rebrowse_once_locked(test_property, db_session, test_user):
    """Fix B: once a property is locked, calling recommend_properties again
    with no new criteria should refuse rather than silently returning
    portfolio-wide matches (this is also independently reachable in a Guest
    Support call, but exercised here via the Lead Agent + locked-state path
    since that's where the closure's own property_id can't already prevent
    it)."""
    state = ConversationState()
    state.lock_property(str(test_property.id), test_property.name)
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params)
    assert test_property.name in params.result
    assert "already looking at" in params.result.lower()


async def test_recommend_properties_allows_explicit_compare_with_new_criteria(test_property, db_session, test_user):
    """A guest explicitly asking to compare/switch ('something in Goa
    instead') supplies new criteria (e.g. preferred_location) -- this must
    still go through to a real portfolio search, not be blocked by the lock."""
    await _second_property(db_session, test_user, name="Palm Retreat")

    state = ConversationState()
    state.lock_property(str(test_property.id), test_property.name)
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, preferred_location="Goa")
    assert "already looking at" not in params.result.lower()


async def test_guest_support_call_search_faq_unaffected_by_conversation_state(test_property, db_session, test_user):
    """Guest Support calls already have a fixed property_id at the closure
    level -- confirm the new fallback chain doesn't break that when no
    conversation_state is passed at all (matches how pipeline.py always
    passes one in practice, but callers/tests may still omit it)."""
    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            property_id=test_property.id,
            question="Is parking free?",
            answer="Yes, free parking on-site.",
            status="verified",
        )
    )
    await db_session.commit()

    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id)
    search_faq = next(t for t in tools if t.__name__ == "search_faq")

    params = _FakeFunctionCallParams()
    await search_faq(params, query="is parking free")
    assert "free parking" in params.result.lower()
