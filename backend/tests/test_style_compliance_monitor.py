"""Covers app/voice/style_compliance_monitor.py -- the streaming observer
that replaced response_compliance.py's ResponseComplianceProcessor entirely
(regeneration removed, buffering removed). StyleComplianceRule.check() is
pinned against the exact real production-call regression turn
(405ee30f-523b-4b9d-bf9d-c4f6e49f6847, 2026-08-07) that originally motivated
this whole mechanism: guest spoke Hindi, the Style Engine correctly recorded
Hindi, and the LLM still replied in plain English.
"""

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.conversation_quality import ConversationQuality, ValidationResult
from app.voice.conversation_state import ConversationState
from app.voice.conversation_style import ConversationAnalyzer, StyleEngine
from app.voice.style_compliance_monitor import StyleComplianceMonitor, StyleComplianceRule

_REAL_REGRESSION_MIRA_REPLY = (
    "Great! To check availability, could you let me know the exact check‑in and "
    "check‑out dates you have in mind, and how many guests will be staying?"
)


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _style_from_turns(*turns: str):
    engine = StyleEngine()
    style = None
    for i, text in enumerate(turns, start=1):
        signal = ConversationAnalyzer.analyze_turn(text)
        style = engine.update(signal, style, turn_index=i)
    return style


# --- StyleComplianceRule: pure function tests ---


def test_reproduces_real_call_regression():
    style = _style_from_turns("हां मैं last week of August की trip plan कर रही हूं।")
    result = StyleComplianceRule().check(_REAL_REGRESSION_MIRA_REPLY, style)

    assert result.severity == "FAIL"
    assert result.metadata["expected_language"] == "hindi"
    assert result.metadata["observed_language"] == "english"


def test_passes_correct_hinglish_reply():
    style = _style_from_turns("हां मैं last week of August की trip plan कर रही हूं।")
    result = StyleComplianceRule().check(
        "Aapka check-in 1 August ko hai, kya main aapki kuch madad kar sakti hoon?", style
    )
    assert result.severity == "INFO"


def test_passes_devanagari_reply_when_hindi_expected():
    style = _style_from_turns("हां मैं September में trip plan कर रही हूं।", "दो लोग रहेंगे")
    result = StyleComplianceRule().check("आपका चेक-इन 1 सितंबर को है, कितने मेहमान रहेंगे?", style)
    assert result.severity == "INFO"


def test_passes_english_reply_when_english_expected():
    style = _style_from_turns("Hello, I'd like to book a room")
    result = StyleComplianceRule().check("Sure, let me check that for you right away.", style)
    assert result.severity == "INFO"


def test_fails_devanagari_reply_when_english_expected():
    style = _style_from_turns("Hello, I'd like to book a room")
    result = StyleComplianceRule().check("आपका चेक-इन कल यानी 1 अगस्त को है, कितने मेहमान रहेंगे?", style)
    assert result.severity == "FAIL"


def test_no_style_known_yet_is_info():
    result = StyleComplianceRule().check("Hello, how can I help you today?", None)
    assert result.severity == "INFO"


def test_short_response_is_warning_not_fail():
    style = _style_from_turns("Hello, I'd like to book a room")
    result = StyleComplianceRule().check("Sure.", style)
    assert result.severity == "WARNING"


def test_check_never_takes_conversation_state_only_style():
    """Architecture requirement: the validator's signature narrows to
    ConversationStyle, never the full ConversationState -- a language
    validator has no legitimate reason to see booking slots, escalation
    flags, or property selections."""
    import inspect

    sig = inspect.signature(StyleComplianceRule.check)
    assert "state" not in sig.parameters
    assert "style" in sig.parameters


# --- StyleComplianceMonitor: streaming behavior ---


@pytest.mark.asyncio
async def test_never_buffers_text_frames_are_forwarded_immediately():
    """Hard requirement: every LLMTextFrame must be forwarded unconditionally
    and unchanged, regardless of what the rule eventually decides -- this
    processor never rewrites, never cuts, never withholds."""
    state = ConversationState()
    state.conversation_style = _style_from_turns("हां मैं trip plan कर रही हूं।")
    monitor = StyleComplianceMonitor(state)

    down_frames, _ = await run_test(monitor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    spoken_text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert spoken_text == _REAL_REGRESSION_MIRA_REPLY


@pytest.mark.asyncio
async def test_forwards_each_chunk_before_the_response_ends():
    """Confirms genuine streaming: a text chunk is forwarded the moment it
    arrives, not held until LLMFullResponseEndFrame."""
    forwarded: list[LLMTextFrame] = []

    class _CollectingMonitor(StyleComplianceMonitor):
        async def push_frame(self, frame, direction=None):
            if isinstance(frame, LLMTextFrame):
                forwarded.append(frame)

    monitor = _CollectingMonitor()
    from pipecat.processors.frame_processor import FrameDirection

    await monitor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await monitor.process_frame(LLMTextFrame("First chunk."), FrameDirection.DOWNSTREAM)

    assert "".join(f.text for f in forwarded) == "First chunk."

    await monitor.process_frame(LLMTextFrame(" Second chunk."), FrameDirection.DOWNSTREAM)
    await monitor.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    assert "".join(f.text for f in forwarded) == "First chunk. Second chunk."


@pytest.mark.asyncio
async def test_no_conversation_state_is_a_no_op_not_an_error():
    monitor = StyleComplianceMonitor()
    down_frames, _ = await run_test(monitor, frames_to_send=_response("Sure, let me check that."))
    assert any(isinstance(f, LLMTextFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_empty_response_does_not_crash():
    monitor = StyleComplianceMonitor()
    down_frames, _ = await run_test(monitor, frames_to_send=_response(""))
    assert len(down_frames) == 3  # start + empty text + end, nothing dropped


# --- ConversationQuality integration ---


@pytest.mark.asyncio
async def test_records_fail_to_conversation_quality():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हां मैं trip plan कर रही हूं।")
    quality = ConversationQuality()
    monitor = StyleComplianceMonitor(state, quality)

    await run_test(monitor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    assert len(quality.validations) == 1
    assert quality.validations[0].rule == "style_compliance"
    assert quality.validations[0].severity == "FAIL"


@pytest.mark.asyncio
async def test_fail_sets_pending_style_correction_on_quality():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हां मैं trip plan कर रही हूं।")
    quality = ConversationQuality()
    monitor = StyleComplianceMonitor(state, quality)

    await run_test(monitor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    assert quality.pending_style_correction is True


@pytest.mark.asyncio
async def test_pass_does_not_set_pending_style_correction():
    state = ConversationState()
    state.conversation_style = _style_from_turns("Hello, I'd like to book a room")
    quality = ConversationQuality()
    monitor = StyleComplianceMonitor(state, quality)

    await run_test(monitor, frames_to_send=_response("Sure, let me check that for you right away."))

    assert quality.pending_style_correction is False


@pytest.mark.asyncio
async def test_works_without_a_quality_object():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हां मैं trip plan कर रही हूं।")
    monitor = StyleComplianceMonitor(state)

    down_frames, _ = await run_test(monitor, frames_to_send=_response(_REAL_REGRESSION_MIRA_REPLY))

    assert any(isinstance(f, LLMTextFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_recorded_validation_result_carries_turn_index():
    state = ConversationState()
    state.conversation_style = _style_from_turns("Hello")
    quality = ConversationQuality()
    monitor = StyleComplianceMonitor(state, quality)

    await run_test(monitor, frames_to_send=_response("Sure."))
    await run_test(monitor, frames_to_send=_response("Sure, one moment please."))

    assert [v.turn_index for v in quality.validations] == [1, 2]


def test_result_is_a_validation_result_instance():
    style = _style_from_turns("Hello")
    result = StyleComplianceRule().check("Sure, one moment.", style)
    assert isinstance(result, ValidationResult)
    assert result.rule == "style_compliance"
    assert result.processing_time_ms >= 0
