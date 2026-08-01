"""Covers Phase 1.3/1.6 (documentation/agent-conversation-improvement.md):
StatePromptSyncProcessor keeps a compact ConversationState summary visible
to the LLM every turn, without ever touching the real system prompt message
(so Groq's prefix-caching on it is preserved) and without growing a stale
list of old state-summary messages.
"""

import pytest
from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState
from app.voice.state_prompt_sync import _STATE_BLOCK_MARKER, StatePromptSyncProcessor, build_state_block_content


def _context_frame(*messages: dict) -> LLMContextFrame:
    return LLMContextFrame(context=LLMContext(messages=list(messages)))


def test_build_state_block_content_empty_when_nothing_known():
    state = ConversationState()
    assert build_state_block_content(state) == ""


def test_build_state_block_content_includes_known_slots():
    state = ConversationState()
    state.set_slot("num_guests", 4)
    state.set_slot("preferred_location", "Goa")
    content = build_state_block_content(state)
    assert "guests: 4" in content
    assert "area: Goa" in content
    assert "Do not re-ask" in content


def test_build_state_block_content_includes_recommendations_shown():
    state = ConversationState()
    state.record_recommendations([{"property_id": "p1", "name": "Ocean View", "price": 6000, "guests": 4}])
    content = build_state_block_content(state)
    assert "Ocean View" in content
    assert "Do not re-list" in content


def test_build_state_block_content_includes_goal_hint():
    state = ConversationState()
    state.mark_escalated()
    content = build_state_block_content(state)
    assert "already been escalated" in content


def test_build_state_block_content_includes_closing_hint_once_farewell_is_pending():
    """Phase 5.2 (documentation/agent-conversation-improvement.md): once
    end_call/decline_irrelevant_call has armed a close (silence_watchdog's
    mark_farewell_pending), the prompt-visible state block must tell the
    model directly not to reopen new topics -- backed by real state, not
    only GOLDEN_RULES' prose instruction."""
    state = ConversationState()
    state.mark_farewell_pending()
    content = build_state_block_content(state)
    assert "closing" in content.lower()
    assert "don't reopen new topics" in content.lower() or "do not reopen" in content.lower()


def test_build_state_block_content_drops_closing_hint_once_reopened():
    """A guest who speaks again before the hangup completes reopens the
    call (silence_watchdog's mark_reopened) -- the closing instruction must
    not linger and confuse the model into thinking the call is still
    wrapping up."""
    state = ConversationState()
    state.mark_farewell_pending()
    state.mark_reopened()
    content = build_state_block_content(state)
    assert "call is closing" not in content.lower()


@pytest.mark.asyncio
async def test_no_op_when_state_has_nothing_to_surface():
    state = ConversationState()
    processor = StatePromptSyncProcessor(state)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"})],
    )

    context_frames = [f for f in down_frames if isinstance(f, LLMContextFrame)]
    assert len(context_frames) == 1
    # Nothing injected -- message list is unchanged.
    assert len(context_frames[0].context.messages) == 2
    assert context_frames[0].context.messages[0]["content"] == "sys"


@pytest.mark.asyncio
async def test_injects_state_block_right_after_system_prompt():
    state = ConversationState()
    state.set_slot("num_guests", 4)
    processor = StatePromptSyncProcessor(state)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"})],
    )

    context_frames = [f for f in down_frames if isinstance(f, LLMContextFrame)]
    messages = context_frames[0].context.messages
    assert len(messages) == 3
    assert messages[0]["content"] == "sys"  # real system prompt untouched
    assert messages[1].get(_STATE_BLOCK_MARKER) is True
    assert "guests: 4" in messages[1]["content"]
    assert messages[2]["content"] == "hi"


@pytest.mark.asyncio
async def test_updates_existing_state_block_in_place_rather_than_appending():
    """A second turn with more slots known must UPDATE the same state-block
    message, never append a second one -- a growing list of stale blocks
    would waste tokens and confuse 'current state' with 'state from 5 turns
    ago'."""
    state = ConversationState()
    state.set_slot("num_guests", 4)
    processor = StatePromptSyncProcessor(state)

    first_frame = _context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"})
    down_frames_1, _ = await run_test(processor, frames_to_send=[first_frame])
    messages_after_1 = down_frames_1[-1].context.messages
    assert len(messages_after_1) == 3

    # Simulate more slots becoming known, and a real new turn appended --
    # the context frame pipecat would actually push next.
    state.set_slot("preferred_location", "Goa")
    second_frame = _context_frame(*messages_after_1, {"role": "user", "content": "we want Goa"})

    down_frames_2, _ = await run_test(processor, frames_to_send=[second_frame])
    messages_after_2 = down_frames_2[-1].context.messages

    state_block_messages = [m for m in messages_after_2 if m.get(_STATE_BLOCK_MARKER)]
    assert len(state_block_messages) == 1
    assert "area: Goa" in state_block_messages[0]["content"]
    assert "guests: 4" in state_block_messages[0]["content"]
    # Real system prompt and conversation turns are untouched/preserved.
    assert messages_after_2[0]["content"] == "sys"
    assert any(m.get("content") == "we want Goa" for m in messages_after_2)


@pytest.mark.asyncio
async def test_real_system_prompt_message_is_never_mutated():
    """Critical for Groq prompt-prefix caching (docs/agents.md) -- the real
    system prompt at messages[0] must stay byte-identical across turns
    regardless of how much conversation state changes."""
    state = ConversationState()
    processor = StatePromptSyncProcessor(state)

    system_content = "GOLDEN_RULES and everything else -- must never change mid-call"
    frame = _context_frame({"role": "system", "content": system_content}, {"role": "user", "content": "hi"})
    await run_test(processor, frames_to_send=[frame])

    state.set_slot("num_guests", 4)
    frame2 = _context_frame({"role": "system", "content": system_content}, {"role": "user", "content": "hi"})
    down_frames, _ = await run_test(processor, frames_to_send=[frame2])

    assert down_frames[-1].context.messages[0]["content"] == system_content


def test_build_state_block_content_includes_passive_language_hint():
    """Phase 3.2: passive per-turn detection (LanguageSyncProcessor writes
    this) surfaces as a continue-in-this-language hint."""
    state = ConversationState()
    state.current_spoken_language = Language.HI
    content = build_state_block_content(state)
    assert "currently speaking Hinglish" in content
    assert "continue replying in Hinglish" in content


def test_build_state_block_content_english_language_hint():
    state = ConversationState()
    state.current_spoken_language = Language.EN_IN
    content = build_state_block_content(state)
    assert "currently speaking English" in content


def test_build_state_block_content_explicit_preference_overrides_passive_detection():
    """Phase 3.3: an explicit, guest-stated request ('can you speak Hindi?')
    is a stronger signal than passive code-switch detection and must win
    when both are set -- e.g. the guest asked for Hindi but their most
    recent utterance happened to be transcribed as English."""
    state = ConversationState()
    state.current_spoken_language = Language.EN
    state.explicit_language_preference = Language.HI
    content = build_state_block_content(state)
    assert "asked you to speak in Hinglish" in content
    assert "currently speaking English" not in content


def test_build_state_block_content_no_language_hint_before_any_speech_detected():
    """The very first turn of a call, before any guest speech has been
    transcribed -- GOLDEN_RULES' own passive-mirroring instruction already
    covers this case, so no hint (and no extra tokens) is injected."""
    state = ConversationState()
    content = build_state_block_content(state)
    assert content == ""


@pytest.mark.asyncio
async def test_language_hint_flows_through_the_real_processor():
    state = ConversationState()
    state.current_spoken_language = Language.HI_IN
    processor = StatePromptSyncProcessor(state)

    frame = _context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "bhai kya haal hai"})
    down_frames, _ = await run_test(processor, frames_to_send=[frame])

    messages = down_frames[-1].context.messages
    state_block = next(m for m in messages if m.get(_STATE_BLOCK_MARKER))
    assert "Hinglish" in state_block["content"]


def test_build_state_block_content_includes_quoted_price():
    """Phase 4.1: a structured fact (real total/dates/property) instead of
    asking the model to recall a specific number from a long transcript."""
    state = ConversationState()
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 18700.0)
    content = build_state_block_content(state)
    assert "You already quoted ₹18,700 for Ocean View Villa" in content
    assert "2026-08-10 to 2026-08-12" in content
    assert "Do not re-quote a different number" in content


def test_build_state_block_content_no_quoted_price_line_when_unset():
    state = ConversationState()
    state.set_slot("num_guests", 4)
    content = build_state_block_content(state)
    assert "already quoted" not in content
