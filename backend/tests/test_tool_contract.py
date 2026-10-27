"""app/voice/tool_contract.py -- the validation + result-envelope contract
every voice-agent tool goes through. Exercised both on a bare decorated
function and on the real tools from build_voice_tools."""

import uuid

from pipecat.adapters.schemas.direct_function import DirectFunctionWrapper

from app.services import tool_handlers
from app.voice.conversation_state import ConversationState
from app.voice.tool_contract import ToolOutcome, _llm_args_model, voice_tool
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self):
        self.calls: list[dict] = []
        self.properties = None
        self.tool_call_id = "tc_test"

    async def result_callback(self, result, properties=None):
        self.calls.append(result)
        self.properties = properties

    @property
    def envelope(self) -> dict:
        assert len(self.calls) == 1, f"expected exactly one result, got {len(self.calls)}"
        return self.calls[0]


def _tools(host_user_id=None, **kwargs):
    return {
        t.__name__: t
        for t in build_voice_tools(
            call_session_id=None,
            property_id=None,
            host_user_id=host_user_id or uuid.uuid4(),
            conversation_state=ConversationState(),
            **kwargs,
        )
    }


# --- Schema / validation alignment ------------------------------------------------


def test_every_tool_is_validated_against_exactly_its_llm_schema():
    """The validation model and the LLM-facing schema come from the same
    signature -- same fields, same required set -- for all 14 tools."""
    tools = _tools()
    assert len(tools) == 14
    for name, fn in tools.items():
        schema = DirectFunctionWrapper(fn).to_function_schema()
        model = _llm_args_model(fn.__wrapped__)
        assert set(schema.properties) == set(model.model_fields), name
        assert set(schema.required) == {k for k, f in model.model_fields.items() if f.is_required()}, name


# --- The decorator on a bare function --------------------------------------------


async def test_valid_call_returns_success_envelope():
    @voice_tool()
    async def echo(params, text: str, times: int = 1):
        await params.result_callback(text * times)

    params = _FakeFunctionCallParams()
    await echo(params, text="hi", times="2")  # "2" coerced to int
    assert params.envelope == {"success": True, "status": "ok", "tool": "echo", "result": "hihi"}


async def test_unsupported_argument_is_rejected_with_valid_names():
    ran = []

    @voice_tool()
    async def echo(params, text: str):
        ran.append(text)
        await params.result_callback(text)

    params = _FakeFunctionCallParams()
    await echo(params, text="hi", nights=1)
    assert ran == []
    assert params.envelope["success"] is False
    assert params.envelope["error_type"] == "invalid_tool_arguments"
    assert "'nights' is not a parameter" in params.envelope["message"]
    assert "Valid parameters: text" in params.envelope["message"]


async def test_alias_maps_a_legacy_name_before_validation():
    @voice_tool(aliases={"budget": "budget_amount"})
    async def search(params, budget_amount: float | None = None):
        await params.result_callback(f"{budget_amount}")

    params = _FakeFunctionCallParams()
    await search(params, budget=7000)
    assert params.envelope["result"] == "7000.0"


async def test_missing_required_argument_is_rejected():
    @voice_tool()
    async def echo(params, text: str):
        await params.result_callback(text)

    params = _FakeFunctionCallParams()
    await echo(params)
    assert params.envelope["error_type"] == "invalid_tool_arguments"
    assert "'text' is required" in params.envelope["message"]


async def test_wrong_type_is_rejected():
    @voice_tool()
    async def count(params, num_guests: int | None = None, amenities: list[str] | None = None):
        await params.result_callback("ok")

    params = _FakeFunctionCallParams()
    await count(params, num_guests="two")
    assert params.envelope["error_type"] == "invalid_tool_arguments"
    assert "num_guests" in params.envelope["message"]

    params = _FakeFunctionCallParams()
    await count(params, amenities="pool")  # a bare string, not a list
    assert params.envelope["error_type"] == "invalid_tool_arguments"


async def test_internal_exception_is_distinct_from_invalid_arguments():
    hook_calls = []

    @voice_tool(on_internal_error=lambda: hook_calls.append(1))
    async def boom(params):
        raise RuntimeError("db exploded")

    params = _FakeFunctionCallParams()
    await boom(params)
    assert params.envelope["success"] is False
    assert params.envelope["error_type"] == "internal_error"
    assert "db exploded" not in params.envelope["message"]  # details stay in logs
    assert "do not invent" in params.envelope["message"]
    assert hook_calls == [1]


async def test_a_tool_that_never_answers_still_yields_exactly_one_result():
    @voice_tool()
    async def silent(params):
        return None

    params = _FakeFunctionCallParams()
    await silent(params)
    assert params.envelope["error_type"] == "internal_error"


async def test_a_second_result_for_the_same_call_is_dropped():
    @voice_tool()
    async def twice(params):
        await params.result_callback("first")
        await params.result_callback("second")

    params = _FakeFunctionCallParams()
    await twice(params)
    assert params.envelope["result"] == "first"


async def test_tool_statuses_map_to_success_flags():
    for outcome, success in [
        (ToolOutcome.ok("x"), True),
        (ToolOutcome.no_results("x"), True),
        (ToolOutcome.needs_clarification("x"), True),
        (ToolOutcome.business_error("x"), False),
    ]:

        @voice_tool()
        async def tool(params, _outcome=outcome):
            await params.result_callback(_outcome)

        params = _FakeFunctionCallParams()
        await tool(params)
        assert params.envelope["success"] is success
        assert params.envelope["status"] == outcome.status


async def test_repeated_invalid_calls_tell_the_model_to_stop():
    @voice_tool()
    async def echo(params, text: str):
        await params.result_callback(text)

    first, second = _FakeFunctionCallParams(), _FakeFunctionCallParams()
    await echo(first, bogus=1)
    await echo(second, bogus=1)
    assert "call it again" in first.envelope["message"]
    assert "Do not call echo again" in second.envelope["message"]


async def test_run_llm_properties_pass_through():
    tools = _tools()
    params = _FakeFunctionCallParams()
    await tools["end_call"](params)
    assert params.envelope["success"] is True
    assert params.properties is not None and params.properties.run_llm is False


# --- Real tools -------------------------------------------------------------------


async def test_off_list_urgency_never_silently_drops_an_escalation(monkeypatch, db_session, test_user):
    """Literal args render as an untyped schema, so the LLM can send
    urgency="urgent". That used to come back as "could you repeat the
    dates?" with no escalation at all; now the model gets the allowed values
    and can re-call correctly."""
    handled = []

    async def _fake_escalate(*args, **kwargs):
        handled.append(args)
        return "escalated"

    monkeypatch.setattr(tool_handlers, "handle_escalate_to_host", _fake_escalate)
    escalate = _tools(test_user.id)["escalate_to_host"]

    params = _FakeFunctionCallParams()
    await escalate(params, reason="guest wants a callback", urgency="urgent")
    assert handled == []
    assert params.envelope["error_type"] == "invalid_tool_arguments"
    assert "'emergency'" in params.envelope["message"]  # allowed values listed

    params = _FakeFunctionCallParams()
    await escalate(params, reason="guest wants a callback", urgency="high")
    assert params.envelope["success"] is True
    assert len(handled) == 1


async def test_business_error_is_not_reported_as_success(db_session, test_user):
    get_pricing = _tools(test_user.id)["get_pricing"]
    params = _FakeFunctionCallParams()
    await get_pricing(params, property_id=str(uuid.uuid4()), num_guests=2, nights=2)
    assert params.envelope["success"] is False
    assert params.envelope["status"] == "business_error"
    assert "couldn't find that property" in params.envelope["message"]


async def test_handler_exception_in_any_tool_returns_internal_error(monkeypatch, db_session, test_user):
    async def _boom(*args, **kwargs):
        raise RuntimeError("faq index down")

    monkeypatch.setattr(tool_handlers, "handle_search_faq", _boom)
    params = _FakeFunctionCallParams()
    await _tools(test_user.id)["search_faq"](params, query="is breakfast included?")
    assert params.envelope["error_type"] == "internal_error"


async def test_update_lead_accepts_both_budget_names(db_session, test_user, test_call_session):
    """budget_amount is the shared name with recommend_properties; the old
    `budget` name maps onto it instead of crashing the call."""
    state = ConversationState()
    tools = {
        t.__name__: t
        for t in build_voice_tools(
            call_session_id=test_call_session.id, property_id=None, host_user_id=test_user.id, conversation_state=state
        )
    }
    params = _FakeFunctionCallParams()
    await tools["update_lead"](params, budget_amount=7000, budget_basis="per_night")
    assert params.envelope["success"] is True
    assert state.slots["budget"] == 7000 and state.slots["budget_basis"] == "per_night"

    params = _FakeFunctionCallParams()
    await tools["update_lead"](params, budget=8000)
    assert params.envelope["success"] is True
    assert state.slots["budget"] == 8000
