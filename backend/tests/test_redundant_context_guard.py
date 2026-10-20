import pytest
from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

from app.voice.redundant_context_guard import RedundantContextGuardProcessor


def _context_frame(*messages: dict) -> LLMContextFrame:
    return LLMContextFrame(context=LLMContext(messages=list(messages)))


@pytest.mark.asyncio
async def test_first_context_frame_always_forwarded():
    guard = RedundantContextGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"})],
    )

    assert len([f for f in down_frames if isinstance(f, LLMContextFrame)]) == 1


@pytest.mark.asyncio
async def test_growing_context_is_always_forwarded():
    """Normal conversation, and a normal fast tool-calling chain -- each
    step adds a new message (a user turn, or a function-call result), so
    every one of these must reach the LLM."""
    guard = RedundantContextGuardProcessor()

    base = [{"role": "system", "content": "sys"}]
    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _context_frame(*base, {"role": "user", "content": "hi"}),
            _context_frame(*base, {"role": "user", "content": "hi"}, {"role": "assistant", "tool_calls": []}),
            _context_frame(
                *base,
                {"role": "user", "content": "hi"},
                {"role": "assistant", "tool_calls": []},
                {"role": "tool", "content": "result"},
            ),
        ],
    )

    assert len([f for f in down_frames if isinstance(f, LLMContextFrame)]) == 3


@pytest.mark.asyncio
async def test_regression_unchanged_context_after_deferred_push_is_dropped():
    """Regression for the exact live failure on 2026-07-26: pipecat's
    deferred context-push (LLMUserAggregator._push_context_on_bot_stopped_speaking)
    fired a second time with the SAME message count as the previous
    completion -- nothing new for the LLM to respond to. That second,
    redundant LLMContextFrame must be dropped, not forwarded to the LLM."""
    guard = RedundantContextGuardProcessor()

    base = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Yes"}]
    same_context = [
        *base,
        {"role": "assistant", "content": "Anything else I can help you with today?"},
    ]

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _context_frame(*same_context),  # the real completion that produced this text
            _context_frame(*same_context),  # spurious re-fire, identical message count
        ],
    )

    context_frames = [f for f in down_frames if isinstance(f, LLMContextFrame)]
    assert len(context_frames) == 1
    assert len(context_frames[0].context.messages) == len(same_context)


@pytest.mark.asyncio
async def test_new_real_turn_after_a_dropped_frame_is_still_forwarded():
    """Suppressing one redundant frame must not get the guard stuck --
    once real new content (a genuine next user turn) arrives, it must go
    through normally."""
    guard = RedundantContextGuardProcessor()

    base = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Yes"}]
    stale = [*base, {"role": "assistant", "content": "Anything else I can help you with today?"}]
    real_next_turn = [*stale, {"role": "user", "content": "Actually, one more question"}]

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _context_frame(*stale),
            _context_frame(*stale),  # dropped
            _context_frame(*real_next_turn),  # must still go through
        ],
    )

    context_frames = [f for f in down_frames if isinstance(f, LLMContextFrame)]
    assert len(context_frames) == 2
    assert len(context_frames[-1].context.messages) == len(real_next_turn)
