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

from app.voice.conversation_quality import ConversationQuality, ValidationResult
from app.voice.conversation_state import ConversationState
from app.voice.conversation_style import ConversationAnalyzer, StyleEngine
from app.voice.state_prompt_sync import StatePromptSyncProcessor, build_state_block_content


def _style_from_turns(*turns: str):
    """Builds a real ConversationStyle via the actual engine, same as a live
    call would produce it -- avoids hand-constructing a ConversationStyle
    dataclass whose shape could silently drift from the real one."""
    engine = StyleEngine()
    style = None
    for i, text in enumerate(turns, start=1):
        signal = ConversationAnalyzer.analyze_turn(text)
        style = engine.update(signal, style, turn_index=i)
    return style


def _is_injected_state_block(message: dict, real_system_content: str) -> bool:
    """Identifies the processor's own injected message by its role/content
    shape alone -- deliberately NOT a marker field, matching the real
    processor (Phase 4.1's P0 fix, see state_prompt_sync.py's module
    docstring: a marker FIELD on the message leaked into the raw Groq
    request body and crashed every completion, confirmed live 2026-08-01)."""
    return message["role"] == "system" and message["content"] != real_system_content


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


def test_build_state_block_content_soft_close_when_guest_never_committed():
    """Phase 8 (Closing intelligence): a guest who never accepted a specific
    property or heard a real price gets soft-close framing -- never implies
    anything is booked/confirmed."""
    state = ConversationState()
    state.mark_farewell_pending()
    content = build_state_block_content(state)
    assert "soft close" in content.lower()
    assert "hard close" not in content.lower()


def test_build_state_block_content_hard_close_when_guest_accepted_property_and_was_quoted():
    """A guest who both accepted a specific property (via lock_property
    after recommend_properties) AND was quoted a real price gets hard-close
    framing -- both facts already tracked by ConversationState, no new
    field. Neither fact alone is enough (see the two tests below)."""
    state = ConversationState()
    state.record_recommendations([{"property_id": "p1", "name": "Ocean View", "price": 6000, "guests": 4}])
    state.lock_property("p1", "Ocean View")
    state.record_quoted_price("Ocean View", "2026-08-10", "2026-08-12", 12000)
    state.mark_farewell_pending()
    content = build_state_block_content(state)
    assert "hard close" in content.lower()
    assert "soft close" not in content.lower()


def test_build_state_block_content_soft_close_when_only_price_quoted_no_property_accepted():
    """Being quoted a price alone (e.g. Guest Support mode, where a property
    is fixed but the guest never explicitly accepted/showed interest) is
    not enough for a hard close -- guest_accepted_property_id must also be
    set, same discipline lock_property's own docstring already applies."""
    state = ConversationState()
    state.record_quoted_price("Ocean View", "2026-08-10", "2026-08-12", 12000)
    state.mark_farewell_pending()
    content = build_state_block_content(state)
    assert "soft close" in content.lower()


def test_build_state_block_content_soft_close_when_only_property_accepted_no_price_quoted():
    """Accepting a property alone, with no price ever actually quoted, is
    not enough for a hard close either -- both facts are required."""
    state = ConversationState()
    state.record_recommendations([{"property_id": "p1", "name": "Ocean View", "price": 6000, "guests": 4}])
    state.lock_property("p1", "Ocean View")
    state.mark_farewell_pending()
    content = build_state_block_content(state)
    assert "soft close" in content.lower()


def test_build_state_block_content_soft_close_when_quote_is_stale_for_a_different_property():
    """Self-review fix: guest_accepted_property_id and quoted_price are both
    sticky (never cleared, only overwritten) -- a guest who accepted/was
    quoted for Ocean View, then explicitly switched to browsing a different
    property, must NOT read as a hard close for the stale Ocean View quote
    just because both fields happen to still be truthy. Only a quote whose
    own property_name matches the CURRENTLY accepted property counts."""
    state = ConversationState()
    state.record_recommendations([{"property_id": "p1", "name": "Ocean View", "price": 6000, "guests": 4}])
    state.lock_property("p1", "Ocean View")
    state.record_quoted_price("Ocean View", "2026-08-10", "2026-08-12", 12000)
    # Guest switches to a different property -- a real, explicitly supported
    # flow ("what about Palm Retreat instead?") -- without a new quote ever
    # being requested for it before the call winds down.
    state.record_recommendations(
        [{"property_id": "p2", "name": "Palm Retreat", "price": 5000, "guests": 4}]
    )
    state.lock_property("p2", "Palm Retreat")
    state.mark_farewell_pending()
    content = build_state_block_content(state)
    assert "soft close" in content.lower()
    assert "hard close" not in content.lower()


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
    assert messages[1]["role"] == "system"
    assert "guests: 4" in messages[1]["content"]
    assert messages[2]["content"] == "hi"


@pytest.mark.asyncio
async def test_injected_message_has_no_extra_keys_beyond_role_and_content():
    """Regression for a confirmed-live P0 (2026-08-01): the injected message
    used to carry a third dict key (a marker field) purely for this
    processor's own internal bookkeeping. That extra key was never meant to
    leave this process, but _FallbackGroqLLMService serializes each message
    dict directly into the Groq API request body -- Groq's schema validator
    rejects ANY unrecognized property on a role:system message outright,
    so every completion from the 2nd turn onward 400'd for the rest of the
    call (the guest heard silence). The fix is architectural, not just this
    one field: the injected message must never carry anything beyond the
    two standard OpenAI-shaped keys every provider actually accepts."""
    state = ConversationState()
    state.set_slot("num_guests", 4)
    processor = StatePromptSyncProcessor(state)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"})],
    )

    messages = down_frames[-1].context.messages
    injected = messages[1]
    assert set(injected.keys()) == {"role", "content"}


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

    state_block_messages = [m for m in messages_after_2 if _is_injected_state_block(m, "sys")]
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


def test_build_state_block_content_includes_conversation_style_block():
    """The dynamic state block now sources its language/tone text from the
    Conversation Style Engine's own ConversationStyle (app/voice/
    conversation_style.py), not raw current_spoken_language/
    explicit_language_preference directly -- those two fields are unchanged
    and still exist (still drive LanguageSyncProcessor's live TTS switch and
    the Response Validator's own checks), but this prompt-rendering consumer
    now reads the hysteresis-smoothed style instead of the raw single-turn
    signal."""
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है", "सितंबर के पहले हफ्ते में")
    content = build_state_block_content(state)
    assert "Conversation Style" in content
    assert "Never abruptly change language." in content


def test_build_state_block_content_english_conversation_style():
    state = ConversationState()
    state.conversation_style = _style_from_turns("Hello, how can I help you")
    content = build_state_block_content(state)
    assert "Language: English" in content


def test_build_state_block_content_no_style_block_before_any_speech_detected():
    """The very first turn of a call, before ConversationStyleProcessor has
    computed anything yet -- no hint (and no extra tokens) is injected."""
    state = ConversationState()
    content = build_state_block_content(state)
    assert content == ""


@pytest.mark.asyncio
async def test_conversation_style_block_flows_through_the_real_processor():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है", "सितंबर के पहले हफ्ते में")
    processor = StatePromptSyncProcessor(state)

    frame = _context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "bhai kya haal hai"})
    down_frames, _ = await run_test(processor, frames_to_send=[frame])

    messages = down_frames[-1].context.messages
    state_block = next(m for m in messages if _is_injected_state_block(m, "sys"))
    assert "Conversation Style" in state_block["content"]


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


# --- ConversationQuality bridge: the one permitted quality -> behavior path ---


def test_pending_style_correction_renders_emphasized_block():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है")
    quality = ConversationQuality()
    quality.pending_style_correction = True

    content = build_state_block_content(state, quality)

    assert "did not match this" in content


def test_no_pending_correction_renders_plain_block():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है")
    quality = ConversationQuality()

    content = build_state_block_content(state, quality)

    assert "did not match this" not in content


def test_consuming_pending_correction_clears_the_flag():
    """The emphasis is a one-turn nudge, not a permanent state -- reading it
    once must clear it so the next turn (assuming no new FAIL) renders the
    plain block again."""
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है")
    quality = ConversationQuality()
    quality.pending_style_correction = True

    build_state_block_content(state, quality)

    assert quality.pending_style_correction is False


def test_no_quality_object_is_a_no_op_not_an_error():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है")
    content = build_state_block_content(state)
    assert "did not match this" not in content


def test_validator_never_writes_prompt_text_directly():
    """Architecture requirement: only render_style_block (owned by
    conversation_style.py) ever produces the correction sentence text --
    ConversationQuality itself carries only a boolean, never free text a
    validator might have authored."""
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="style_compliance", severity="FAIL", confidence=0.9))
    assert isinstance(quality.pending_style_correction, bool)
    assert not hasattr(quality, "correction_text")


@pytest.mark.asyncio
async def test_pending_correction_flows_through_the_real_processor():
    state = ConversationState()
    state.conversation_style = _style_from_turns("हाँ मुझे बुकिंग करनी है")
    quality = ConversationQuality()
    quality.pending_style_correction = True
    processor = StatePromptSyncProcessor(state, quality)

    frame = _context_frame({"role": "system", "content": "sys"}, {"role": "user", "content": "bhai kya haal hai"})
    down_frames, _ = await run_test(processor, frames_to_send=[frame])

    messages = down_frames[-1].context.messages
    state_block = next(m for m in messages if _is_injected_state_block(m, "sys"))
    assert "did not match this" in state_block["content"]
    assert quality.pending_style_correction is False
