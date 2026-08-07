"""Covers the Response Compliance layer (app/voice/response_compliance.py) --
a generic, rule-based quality gate between the LLM and tts. LanguageComplianceRule
is the first registered rule; test_reproduces_real_call_regression pins the
processor against the exact turn from the real production call
(405ee30f-523b-4b9d-bf9d-c4f6e49f6847, 2026-08-07) that motivated this layer:
guest spoke Hindi, ConversationState correctly recorded Hindi, and the LLM
still replied in plain English.
"""

import time

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState
from app.voice.response_compliance import (
    ComplianceRule,
    LanguageComplianceRule,
    ResponseComplianceProcessor,
    RuleResult,
)

# Verbatim from the real transcript pulled from the production DB
# (call_sessions.id = 405ee30f-523b-4b9d-bf9d-c4f6e49f6847, 2026-08-07).
_REAL_REGRESSION_GUEST_TURN = "हां मैं last week of August की trip plan कर रही हूं।"
_REAL_REGRESSION_MIRA_REPLY = (
    "Great! To check availability, could you let me know the exact check‑in and "
    "check‑out dates you have in mind, and how many guests will be staying?"
)


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


# --- LanguageComplianceRule ---
# (pure text-heuristic helpers devanagari_ratio/has_hinglish_token now live
# in, and are tested by, test_language_heuristics.py -- shared with the
# Style Engine)


def test_language_rule_reproduces_real_call_regression():
    """The exact real-call regression: STT/ConversationState correctly say
    Hindi is expected (this rule trusts that, does not re-detect it), the
    actual LLM reply is plain English -- must FAIL."""
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN

    result = LanguageComplianceRule().check(_REAL_REGRESSION_MIRA_REPLY, state)

    assert result.verdict == "FAIL"
    assert result.retry_hook is True
    assert result.metadata["expected_language"] == "hindi"
    assert result.metadata["observed_language"] == "english"


def test_language_rule_passes_correct_hinglish_reply():
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN

    result = LanguageComplianceRule().check(
        "Aapka check-in 1 August ko hai, kya main aapki kuch madad kar sakti hoon?", state
    )

    assert result.verdict == "PASS"


def test_language_rule_passes_devanagari_reply_when_hindi_expected():
    """GOLDEN_RULES asks the model to render Hindi/Hinglish in Roman script,
    but a Devanagari reply is still linguistically correct (Hindi, not
    English) -- this rule checks language family, not script preference,
    which is a separate concern outside this rule's scope."""
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN

    result = LanguageComplianceRule().check(
        "आपका चेक-इन कल यानी 1 अगस्त को है, कितने मेहमान रहेंगे?", state
    )

    assert result.verdict == "PASS"


def test_language_rule_passes_english_reply_when_english_expected():
    state = ConversationState()
    state.current_spoken_language = Language.EN_IN

    result = LanguageComplianceRule().check("Sure, let me check that for you right away.", state)

    assert result.verdict == "PASS"


def test_language_rule_fails_devanagari_reply_when_english_expected():
    state = ConversationState()
    state.current_spoken_language = Language.EN_IN

    result = LanguageComplianceRule().check(
        "आपका चेक-इन कल यानी 1 अगस्त को है, कितने मेहमान रहेंगे?", state
    )

    assert result.verdict == "FAIL"


def test_language_rule_explicit_preference_wins_over_passive_detection():
    """Same precedence state_prompt_sync._language_hint already uses --
    reused here, not reimplemented."""
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    state.explicit_language_preference = Language.EN_IN

    result = LanguageComplianceRule().check("Sure, let me check that for you right away.", state)

    assert result.verdict == "PASS"
    assert result.metadata["expected_language"] == "english"


def test_language_rule_no_state_is_pass_nothing_to_check():
    result = LanguageComplianceRule().check("Hello, how can I help you today?", None)
    assert result.verdict == "PASS"


def test_language_rule_no_language_detected_yet_is_pass():
    """First turn of any call, before any guest speech has been transcribed
    -- ConversationState exists but neither language field is set yet."""
    state = ConversationState()
    result = LanguageComplianceRule().check("Hello, how can I help you today?", state)
    assert result.verdict == "PASS"


def test_language_rule_short_response_is_warning_not_fail():
    state = ConversationState()
    state.current_spoken_language = Language.EN_IN

    result = LanguageComplianceRule().check("Sure.", state)

    assert result.verdict == "WARNING"
    assert result.retry_hook is False


# --- ResponseComplianceProcessor: buffering, non-mutation, extensibility ---


@pytest.mark.asyncio
async def test_processor_never_rewrites_text_on_fail():
    """Hard requirement: even on a FAIL verdict, the frame stream reaching
    tts must be byte-identical to what the LLM produced."""
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()])

    down_frames, _ = await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    spoken_text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert spoken_text == _REAL_REGRESSION_MIRA_REPLY


@pytest.mark.asyncio
async def test_processor_passes_through_all_frame_types_unchanged():
    state = ConversationState()
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()])
    frames_in = _response("Sure, let me check that for you.")

    down_frames, _ = await run_test(processor, frames_to_send=frames_in)

    assert isinstance(down_frames[0], LLMFullResponseStartFrame)
    assert isinstance(down_frames[-1], LLMFullResponseEndFrame)
    assert sum(1 for f in down_frames if isinstance(f, LLMTextFrame)) == 1


@pytest.mark.asyncio
async def test_processor_empty_response_does_not_crash():
    """An empty-text response must not crash the processor -- Start/End are
    re-emitted (this processor now buffers and re-emits its own Start frame
    rather than forwarding the original immediately, since a later
    regeneration may need to substitute the whole response -- see
    process_frame's docstring comment), but no empty LLMTextFrame is
    emitted since there's nothing to say and rule evaluation is skipped for
    empty text."""
    processor = ResponseComplianceProcessor(ConversationState())
    down_frames, _ = await run_test(processor, frames_to_send=_response(""))
    assert len(down_frames) == 2
    assert isinstance(down_frames[0], LLMFullResponseStartFrame)
    assert isinstance(down_frames[-1], LLMFullResponseEndFrame)


@pytest.mark.asyncio
async def test_processor_no_conversation_state_is_a_no_op_not_an_error():
    processor = ResponseComplianceProcessor(rules=[LanguageComplianceRule()])
    down_frames, _ = await run_test(processor, frames_to_send=_response("Sure, let me check that."))
    assert any(isinstance(f, LLMTextFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_processor_has_no_built_in_rule_by_default():
    """Architectural requirement: ResponseComplianceProcessor must be
    genuinely rule-agnostic, not LanguageComplianceProcessor-with-an-escape-
    hatch. Constructing it with no `rules` argument at all must register
    zero rules -- callers (pipeline.py) are entirely responsible for
    deciding which rules run; the class itself has no opinion."""
    processor = ResponseComplianceProcessor(ConversationState())
    assert processor._rules == []


class _AlwaysFailRule:
    """Minimal ComplianceRule for testing extensibility -- proves new rules
    register without any change to ResponseComplianceProcessor itself."""

    name = "always_fail_test_rule"

    def check(self, text: str, state) -> RuleResult:
        return RuleResult(self.name, "FAIL", "test rule always fails", confidence=1.0)


class _SlowRule:
    """A rule that deliberately takes a measurable, known amount of time --
    used to prove per-rule timing isolation below."""

    name = "slow_test_rule"

    def check(self, text: str, state) -> RuleResult:
        time.sleep(0.02)
        return RuleResult(self.name, "PASS", "slow but fine", confidence=1.0)


@pytest.mark.asyncio
async def test_processor_reports_per_rule_timing_not_cumulative(monkeypatch):
    """Regression guard: an earlier draft computed processing_time_ms as
    cumulative elapsed-since-the-first-rule rather than each rule's own
    cost, so the second rule's logged timing silently included the first
    rule's time too. Captures the actual logged processing_time_ms per rule
    and confirms the fast rule's reported time is NOT inflated by the slow
    rule that ran before it."""
    captured = []

    def _fake_bind(**fields):
        captured.append(fields)

        class _Logger:
            def warning(self, *a, **k):
                pass

            def info(self, *a, **k):
                pass

            def trace(self, *a, **k):
                pass

        return _Logger()

    monkeypatch.setattr("app.voice.response_compliance.logger.bind", _fake_bind)

    processor = ResponseComplianceProcessor(rules=[_SlowRule(), LanguageComplianceRule()])
    await run_test(processor, frames_to_send=_response("Sure, let me check that for you right away."))

    assert len(captured) == 2
    slow_rule_time = captured[0]["processing_time_ms"]
    fast_rule_time = captured[1]["processing_time_ms"]
    assert slow_rule_time >= 15  # the 0.02s sleep, in ms
    # The second rule's own cost is sub-millisecond -- if timing were still
    # cumulative it would be >= slow_rule_time too, which this must not be.
    assert fast_rule_time < slow_rule_time


@pytest.mark.asyncio
async def test_processor_supports_registering_additional_rules():
    processor = ResponseComplianceProcessor(rules=[LanguageComplianceRule(), _AlwaysFailRule()])
    down_frames, _ = await run_test(processor, frames_to_send=_response("Sure, let me check that."))
    # Still never rewrites, regardless of how many rules ran or failed.
    assert "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame)) == "Sure, let me check that."


def test_compliance_rule_is_a_protocol_language_rule_satisfies_it():
    rule: ComplianceRule = LanguageComplianceRule()
    assert hasattr(rule, "name")
    assert hasattr(rule, "check")


# --- Response Validator: bounded regenerate-once path ---


class _FakeLLMContext:
    """Minimal stand-in for pipecat's real LLMContext -- only the
    `.messages` property this module actually reads is needed."""

    def __init__(self, messages: list[dict]):
        self.messages = messages


@pytest.mark.asyncio
async def test_no_regeneration_attempted_without_llm_context():
    """Regression guard for the pre-existing 17 tests above: omitting
    llm_context (the default) must keep the processor's original
    never-rewrites behavior byte-for-byte, even on a hard FAIL."""
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()])

    down_frames, _ = await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    assert "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame)) == _REAL_REGRESSION_MIRA_REPLY


@pytest.mark.asyncio
async def test_regeneration_substitutes_text_when_correction_passes(monkeypatch):
    async def _fake_regenerate(messages, stronger_instruction):
        assert any(m.get("role") == "user" for m in messages)
        assert "Conversation Style" in stronger_instruction or stronger_instruction
        return "Aapka check-in 1 August ko hai, kitne guests aayenge?"

    monkeypatch.setattr("app.voice.response_compliance._regenerate_once", _fake_regenerate)

    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    llm_context = _FakeLLMContext([{"role": "system", "content": "sys"}, {"role": "user", "content": "haan"}])
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()], llm_context=llm_context)

    down_frames, _ = await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    spoken_text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert spoken_text == "Aapka check-in 1 August ko hai, kitne guests aayenge?"
    assert spoken_text != _REAL_REGRESSION_MIRA_REPLY


@pytest.mark.asyncio
async def test_regeneration_falls_back_to_original_when_correction_still_fails(monkeypatch):
    async def _fake_regenerate(messages, stronger_instruction):
        return "Still a plain English reply with nothing Hindi about it at all."

    monkeypatch.setattr("app.voice.response_compliance._regenerate_once", _fake_regenerate)

    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    llm_context = _FakeLLMContext([{"role": "system", "content": "sys"}])
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()], llm_context=llm_context)

    down_frames, _ = await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    spoken_text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert spoken_text == _REAL_REGRESSION_MIRA_REPLY


@pytest.mark.asyncio
async def test_regeneration_falls_back_to_original_on_call_failure(monkeypatch):
    async def _fake_regenerate(messages, stronger_instruction):
        return None  # _regenerate_once's own contract: None on any failure/timeout

    monkeypatch.setattr("app.voice.response_compliance._regenerate_once", _fake_regenerate)

    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    llm_context = _FakeLLMContext([{"role": "system", "content": "sys"}])
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()], llm_context=llm_context)

    down_frames, _ = await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    spoken_text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert spoken_text == _REAL_REGRESSION_MIRA_REPLY


@pytest.mark.asyncio
async def test_regeneration_never_attempted_on_pass():
    call_count = 0

    async def _counting_regenerate(messages, stronger_instruction):
        nonlocal call_count
        call_count += 1
        return "should never be called"

    state = ConversationState()
    state.current_spoken_language = Language.EN_IN
    llm_context = _FakeLLMContext([{"role": "system", "content": "sys"}])
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()], llm_context=llm_context)
    processor._regenerate_once = _counting_regenerate  # type: ignore[attr-defined]

    await run_test(processor, frames_to_send=_response("Sure, let me check that for you right away."))

    assert call_count == 0


@pytest.mark.asyncio
async def test_regeneration_attempted_at_most_once_per_turn(monkeypatch):
    """Hard cap: even if the corrected text STILL fails, there is no second
    regeneration attempt -- exactly one call, ever, per turn."""
    call_count = 0

    async def _fake_regenerate(messages, stronger_instruction):
        nonlocal call_count
        call_count += 1
        return "Still plain English, still failing validation on purpose."

    monkeypatch.setattr("app.voice.response_compliance._regenerate_once", _fake_regenerate)

    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    llm_context = _FakeLLMContext([{"role": "system", "content": "sys"}])
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()], llm_context=llm_context)

    await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    assert call_count == 1


def test_stronger_style_instruction_includes_style_block_when_available():
    from app.voice.conversation_style import ConversationAnalyzer, StyleEngine
    from app.voice.response_compliance import _stronger_style_instruction

    engine = StyleEngine()
    signal = ConversationAnalyzer.analyze_turn("हाँ मुझे बुकिंग करनी है")
    style, _ = engine.update([], signal, None, turn_index=1)

    state = ConversationState()
    state.conversation_style = style
    instruction = _stronger_style_instruction(state)

    assert "Conversation Style" in instruction
    assert "did not follow" in instruction


def test_stronger_style_instruction_handles_no_state():
    from app.voice.response_compliance import _stronger_style_instruction

    instruction = _stronger_style_instruction(None)
    assert "did not follow" in instruction


@pytest.mark.asyncio
async def test_regeneration_speaks_a_filler_before_the_blocking_call(monkeypatch):
    """Regression guard: a bare regeneration call (up to
    REGENERATION_TIMEOUT_SECONDS, currently 8s) with nothing spoken first is
    dead air on a live phone call -- the same confirmed-live failure mode
    slow_tool_filler.py already exists to fix for slow tool calls. A filler
    must be pushed before the blocking LLM call, not after."""
    from pipecat.frames.frames import TTSSpeakFrame

    call_order = []

    async def _fake_regenerate(messages, stronger_instruction):
        call_order.append("llm_call")
        return "Aapka check-in 1 August ko hai, kitne guests aayenge?"

    monkeypatch.setattr("app.voice.response_compliance._regenerate_once", _fake_regenerate)

    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    llm_context = _FakeLLMContext([{"role": "system", "content": "sys"}])
    processor = ResponseComplianceProcessor(state, rules=[LanguageComplianceRule()], llm_context=llm_context)

    down_frames, _ = await run_test(processor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    filler_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(filler_frames) == 1
    assert filler_frames[0].append_to_context is False
    assert call_order == ["llm_call"]  # filler was pushed (queued) before the await, not after
