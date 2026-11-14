"""The collect gate and area lock as the live Lead Agent tools see them."""

from datetime import date, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User
from app.voice.conversation_state import ConversationState
from app.voice.tools import build_voice_tools


class _Params:
    def __init__(self, guest_says: str | None = None):
        self.result = None
        self.envelope = None
        if guest_says is not None:
            self.context = _Context(guest_says)

    async def result_callback(self, result, properties=None):
        self.envelope = result
        self.result = result.get("result", result.get("message")) if isinstance(result, dict) else result


class _Context:
    def __init__(self, text):
        self._messages = [{"role": "user", "content": text}]

    def get_messages(self):
        return self._messages


def _tool(state, user_id, name, call_session_id=None, property_id=None):
    tools = build_voice_tools(
        call_session_id=call_session_id, property_id=property_id, host_user_id=user_id, conversation_state=state
    )
    return next(t for t in tools if t.__name__ == name)


def _dates():
    today = date.today()
    return (today + timedelta(days=10)).isoformat(), (today + timedelta(days=12)).isoformat()


async def _set_agent_config(user_id, config):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        user.agent_config = config
        await db.commit()


async def _lead_for(call_session_id):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(Lead).where(Lead.call_session_id == call_session_id))


async def test_pricing_waits_for_name_and_phone_but_the_lead_is_still_saved(
    test_user, test_property, test_call_session
):
    state = ConversationState()
    check_in, check_out = _dates()
    params = _Params()
    await _tool(state, test_user.id, "get_pricing", test_call_session.id)(
        params, property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2
    )
    assert params.envelope["status"] == "needs_clarification"
    assert "their name" in params.result and "phone number" in params.result
    assert "total" not in params.result.lower()  # no price leaked

    lead = await _lead_for(test_call_session.id)
    assert lead is not None, "a blocked call must still preserve the guest's interest"
    assert test_property.name in lead.properties_discussed
    assert lead.lead_temperature in ("hot", "very_hot")  # asked for a price


async def test_availability_and_discount_are_gated_the_same_way(test_user, test_property, test_call_session):
    state = ConversationState()
    check_in, check_out = _dates()
    calendar = _Params()
    await _tool(state, test_user.id, "check_calendar", test_call_session.id)(
        calendar, property_id=str(test_property.id), check_in=check_in, check_out=check_out
    )
    assert calendar.envelope["status"] == "needs_clarification"
    state.turn_index += 1
    negotiate = _Params()
    await _tool(state, test_user.id, "negotiate_rate", test_call_session.id)(
        negotiate, property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000
    )
    assert negotiate.envelope["status"] == "needs_clarification"


async def test_pricing_runs_once_name_and_phone_are_known(test_user, test_property):
    state = ConversationState()
    state.slots.update({"guest_name": "Asha", "phone": "+919999999999"})
    check_in, check_out = _dates()
    params = _Params()
    await _tool(state, test_user.id, "get_pricing")(
        params, property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2
    )
    assert params.envelope["status"] == "ok" and "total" in params.result.lower()


async def test_after_two_asks_pricing_proceeds_and_the_host_is_told_what_is_missing(
    test_user, test_property, test_call_session
):
    state = ConversationState()
    check_in, check_out = _dates()
    statuses = []
    for turn in (1, 2, 3):
        state.turn_index = turn
        params = _Params()
        await _tool(state, test_user.id, "get_pricing", test_call_session.id)(
            params, property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2
        )
        statuses.append(params.envelope["status"])
    assert statuses == ["needs_clarification", "needs_clarification", "ok"]  # asked twice, then carried on
    assert set(state.waived_fields) == {"guest_name", "phone"}

    lead = await _lead_for(test_call_session.id)
    assert "did not give" in lead.next_follow_up and "phone number" in lead.next_follow_up


async def test_a_host_must_have_blocks_recommendations_until_given(test_user, test_property):
    await _set_agent_config(test_user.id, {"collect": {"budget": "required"}})
    state = ConversationState()
    blocked = _Params()
    await _tool(state, test_user.id, "recommend_properties")(blocked, preferred_location="Goa")
    assert blocked.envelope["status"] == "needs_clarification" and "budget" in blocked.result

    allowed = _Params()
    await _tool(state, test_user.id, "recommend_properties")(
        allowed, preferred_location="Goa", budget_amount=9000, budget_basis="per_night"
    )
    assert allowed.envelope["status"] != "needs_clarification"


async def test_a_default_host_can_still_get_recommendations_with_nothing_collected(test_user, test_property):
    params = _Params()
    await _tool(ConversationState(), test_user.id, "recommend_properties")(params, preferred_location="Goa")
    assert params.envelope["status"] != "needs_clarification"


async def test_guest_support_calls_are_never_gated(test_user, test_property):
    """A call bound to one property answers questions; it collects nothing."""
    state = ConversationState()
    check_in, check_out = _dates()
    params = _Params()
    await _tool(state, test_user.id, "check_calendar", property_id=test_property.id)(
        params, property_id=str(test_property.id), check_in=check_in, check_out=check_out
    )
    assert params.envelope["status"] == "ok"


async def test_unknown_property_is_reported_as_such_not_masked_by_the_gate(test_user):
    import uuid

    params = _Params()
    await _tool(ConversationState(), test_user.id, "get_pricing")(
        params, property_id=str(uuid.uuid4()), nights=2, num_guests=2
    )
    assert params.envelope["status"] == "business_error"


async def test_area_lock_holds_back_a_switch_the_guest_did_not_ask_for(test_user):
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                Property(user_id=test_user.id, name="Goa Villa", city="Goa", base_price=5000, max_guests=4),
                Property(user_id=test_user.id, name="Jaipur Haveli", city="Jaipur", base_price=5000, max_guests=4),
            ]
        )
        await db.commit()
    state = ConversationState()
    state.set_slot("preferred_location", "Goa")
    recommend = _tool(state, test_user.id, "recommend_properties")

    held = _Params(guest_says="something with a nice view please")
    await recommend(held, preferred_location="Jaipur")
    assert held.envelope["status"] == "needs_clarification"
    assert "still looking in Goa" in held.result
    assert state.slots["preferred_location"] == "Goa"  # lock untouched

    # Same guest turn: still held, not waved through by repetition.
    again = _Params(guest_says="something with a nice view please")
    await recommend(again, preferred_location="Jaipur")
    assert again.envelope["status"] == "needs_clarification"

    # A later turn with the agent still sure: the area switches.
    state.turn_index += 1
    switched = _Params(guest_says="yes")
    await recommend(switched, preferred_location="Jaipur")
    assert switched.envelope["status"] != "needs_clarification"
    assert state.slots["preferred_location"] == "Jaipur"


async def test_area_lock_lets_the_guest_change_area_and_ignores_same_area_wording(test_user):
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                Property(user_id=test_user.id, name="Goa Villa", city="Goa", base_price=5000, max_guests=4),
                Property(user_id=test_user.id, name="Jaipur Haveli", city="Jaipur", base_price=5000, max_guests=4),
            ]
        )
        await db.commit()
    state = ConversationState()
    state.set_slot("preferred_location", "North Goa")
    recommend = _tool(state, test_user.id, "recommend_properties")

    same = _Params(guest_says="what else do you have")
    await recommend(same, preferred_location="Goa")  # "Goa" is inside "North Goa"
    assert same.envelope["status"] != "needs_clarification"

    said = _Params(guest_says="actually, can you show me Jaipur")
    await recommend(said, preferred_location="Jaipur")
    assert said.envelope["status"] != "needs_clarification"


async def _two_cities(user_id):
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                Property(user_id=user_id, name="Goa Villa", city="Goa", base_price=5000, max_guests=4),
                Property(user_id=user_id, name="Goa Annex", city="Gokarna", base_price=5000, max_guests=4),
            ]
        )
        await db.commit()


async def test_area_match_is_whole_word_not_a_substring_of_other_words(test_user):
    """'I have a goal' contains 'goa' but the guest did not ask for Goa."""
    await _two_cities(test_user.id)
    state = ConversationState()
    state.set_slot("preferred_location", "Gokarna")
    params = _Params(guest_says="my goal is a quiet week")
    await _tool(state, test_user.id, "recommend_properties")(params, preferred_location="Goa")
    assert params.envelope["status"] == "needs_clarification"


async def test_area_check_fails_open_when_the_guest_spoke_in_another_script(test_user):
    """The guest may have said 'जयपुर'; a text match cannot verify that, so the
    search is not held back."""
    await _two_cities(test_user.id)
    state = ConversationState()
    state.set_slot("preferred_location", "Gokarna")
    params = _Params(guest_says="मुझे गोवा दिखाइए")
    await _tool(state, test_user.id, "recommend_properties")(params, preferred_location="Goa")
    assert params.envelope["status"] != "needs_clarification"


async def test_nothing_is_looked_up_when_every_needed_detail_is_known(test_user, test_property, monkeypatch):
    """The common path must not pay for a property lookup just to check a gate."""
    from app.services import tool_handlers

    lookups = []
    real = tool_handlers._get_property

    async def counting(db, property_id):
        lookups.append(property_id)
        return await real(db, property_id)

    state = ConversationState()
    state.slots.update({"guest_name": "Asha", "phone": "+919999999999"})
    monkeypatch.setattr(tool_handlers, "_get_property", counting)
    check_in, check_out = _dates()
    params = _Params()
    await _tool(state, test_user.id, "check_calendar")(
        params, property_id=str(test_property.id), check_in=check_in, check_out=check_out
    )
    assert params.envelope["status"] == "ok"
    assert len(lookups) == 1  # the tool's own lookup only, none added by the gate


async def test_area_lock_treats_a_respelled_locality_as_the_locked_area(test_user):
    """The slot holds the host's "Indiranagar"; the model next passes the guest's "Indira Nagar". Same area:
    the search must not be held back to ask whether they have changed their mind."""
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                Property(user_id=test_user.id, name="Jugglers Rest", city="Bengaluru", locality="Indiranagar",
                         locality_key="indiranagar", base_price=5000, max_guests=4),
                Property(user_id=test_user.id, name="Goa Villa", city="Goa", base_price=5000, max_guests=4),
            ]
        )
        await db.commit()
    state = ConversationState()
    state.set_slot("preferred_location", "Indiranagar")
    recommend = _tool(state, test_user.id, "recommend_properties")

    respelled = _Params(guest_says="show me more please")
    await recommend(respelled, preferred_location="Indira Nagar")
    assert respelled.envelope["status"] != "needs_clarification"

    different = _Params(guest_says="show me more please")
    await recommend(different, preferred_location="Goa")
    assert different.envelope["status"] == "needs_clarification"


async def test_the_slot_stores_the_hosts_spelling_of_the_area(test_user):
    async with AsyncSessionLocal() as db:
        db.add(Property(user_id=test_user.id, name="Jugglers Rest", city="Bengaluru", locality="Indiranagar",
                        locality_key="indiranagar", base_price=5000, max_guests=4))
        await db.commit()
    state = ConversationState()

    await _tool(state, test_user.id, "recommend_properties")(_Params(guest_says="Indira Nagar"), preferred_location="Indira Nagar")

    assert state.slots["preferred_location"] == "Indiranagar"
