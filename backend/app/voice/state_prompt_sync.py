"""Injects a compact, always-current summary of ConversationState (Phase 1,
documentation/agent-conversation-improvement.md) into the LLM context right
before each completion -- what's already been collected (state.slots) and
what Mira is currently trying to achieve (state.conversation_goal).

Why this exists: the system prompt (build_system_prompt/build_lead_system_prompt,
app/prompts/system_prompt.py) is built once, before the pipeline/ConversationState
even exist for this call, and never rebuilt afterward -- context.messages[0]
(the system prompt) is fixed for the life of the call. Everything Phase 1's
ConversationState tracks only becomes known mid-call (a guest states their
guest count in turn 2; nothing about that exists at prompt-build time). This
processor is what makes the state Phase 1 tracks actually visible to the
model on every subsequent turn, without ever touching or rebuilding the
static system-prompt message.

Deliberately does NOT touch context.messages[0] (the real system prompt) --
that message must stay byte-identical across the whole call so Groq's
prefix-based prompt caching (see docs/agents.md's Groq section and the open
finding in documentation/project_state.md about cache-read tokens) still
gets a cache hit on it. Instead, this maintains exactly ONE additional
system-role message, inserted right after context.messages[0], updated IN
PLACE on every turn rather than appended fresh each time -- a growing list
of stale state-summary messages would waste tokens and eventually confuse
"most recent state" with "some state from 8 turns ago". Found via object
IDENTITY (self._last_message is the exact same dict this processor itself
inserted last time), not any marker field on the message -- see the
confirmed-live bug below for why a marker field is unsafe here.

Confirmed live 2026-08-01 (P0): an earlier version tagged the message with a
SEPARATE dict key (`{"role": "system", "content": ...,
"__mira_conversation_state_block__": True}`) so `process_frame` could scan
`messages` for its own previous entry. That extra key was meant purely as
internal bookkeeping -- but `_FallbackGroqLLMService` serializes each
message dict directly into the Groq API request body, so the extra key rode
along too. Groq's schema validator rejects any unrecognized property on a
`role: system` message outright: "'messages.1' : for 'role:system' the
following must be satisfied[('messages.1' : property
'__mira_conversation_state_block__' is unsupported)]" -- a 400 on EVERY
completion from the moment the state block first gets injected (the 2nd
real turn onward) for the rest of the call, with no spoken response at all
(the guest hears silence; `_FallbackGroqLLMService`'s model-fallback chain
doesn't help, since every fallback model hits the identical validation error
on the identical malformed request body). Fixed by dropping the marker field
entirely and tracking the inserted message by Python object identity
instead -- `messages` is the SAME list object across turns (pipecat mutates
it in place, never rebuilds it), so `self._last_message is m` reliably finds
the processor's own prior entry with zero extra fields on the message dict
that could leak into any provider's request body, on Groq or anywhere else.

Sits right before llm (after redundant_context_guard, same reasoning as that
processor's own placement -- it needs to see the LLMContextFrame that's
actually about to be forwarded). Inert (a no-op, zero tokens added) whenever
ConversationState has nothing worth surfacing yet (no slots known, greeting
goal) -- the common case for the first turn or two of any call.
"""

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState

# Phase 3.2 (documentation/agent-conversation-improvement.md): Sarvam tags
# codemixed Hindi/English speech as Hindi (same fallback language_sync.py's
# own _HINDI_LANGUAGES set already relies on) -- from the LLM's reply-
# language perspective this reads as "Hinglish", not "Hindi", since
# GOLDEN_RULES already asks for casual Hinglish (never pure/shuddh Hindi)
# whenever the guest is in this language family. Named "English" rather
# than "en-IN" for the same reason every other value in this module is
# plain English words, not enum/locale codes.
_LANGUAGE_DISPLAY_NAMES: dict[Language, str] = {
    Language.EN: "English",
    Language.EN_IN: "English",
    Language.HI: "Hinglish",
    Language.HI_IN: "Hinglish",
}

_GOAL_HINTS: dict[str, str] = {
    "greeting": "",
    "collecting_dates": "Still need the guest's travel dates before recommending or checking availability.",
    "collecting_guests": "Still need the number of guests before recommending or checking availability.",
    "collecting_location_or_purpose": "Still need the guest's preferred area or purpose of stay before recommending.",
    "recommending": "Enough is known to recommend properties now -- do that before asking another question.",
    "awaiting_selection": "Properties have already been shown -- help the guest decide, don't re-list them unprompted.",
    "checking_availability": "Availability/pricing is being checked for the property currently under discussion.",
    "negotiating": "The guest is negotiating on price for the property currently under discussion.",
    "collecting_lead_contact": "The guest has shown interest -- collect name/phone if not already known.",
    "escalating": "This call has already been escalated to the host -- keep helping normally, don't escalate again for the same matter.",
    "closing": "The call is closing -- don't reopen new topics unless the guest raises something new.",
}

_SLOT_LABELS: dict[str, str] = {
    "check_in": "check-in",
    "check_out": "check-out",
    "num_guests": "guests",
    "budget": "budget",
    "preferred_location": "area",
    "purpose_of_stay": "purpose",
    "phone": "phone",
    "guest_name": "name",
}


def _format_slots(slots: dict) -> str:
    parts = []
    for key, label in _SLOT_LABELS.items():
        if key in slots and slots[key] is not None:
            value = slots[key]
            if key == "budget":
                value = f"~₹{value:,.0f}"
            parts.append(f"{label}: {value}")
    return ", ".join(parts)


def _language_hint(state: ConversationState) -> str:
    """Phase 3.2/3.3: an explicit, guest-stated preference (Phase 3.3) always
    wins over passive per-turn detection (Phase 3.1) -- a guest saying
    "can you speak Hindi?" is a stronger, more deliberate signal than
    whatever code-switching happened to be detected on their last utterance.
    Falls back to "" (no hint at all) when neither is set, e.g. the very
    first turn of a call before any guest speech has been transcribed yet --
    GOLDEN_RULES' own passive-mirroring instruction already covers that case."""
    if state.explicit_language_preference is not None:
        name = _LANGUAGE_DISPLAY_NAMES.get(state.explicit_language_preference)
        if name:
            return f"The guest has asked you to speak in {name} -- honor this for the rest of the call."
        return ""
    if state.current_spoken_language is not None:
        name = _LANGUAGE_DISPLAY_NAMES.get(state.current_spoken_language)
        if name:
            return f"The guest is currently speaking {name} -- continue replying in {name} unless they switch."
    return ""


def build_state_block_content(state: ConversationState) -> str:
    """Returns "" when there's nothing worth surfacing yet (no slots known,
    still at the default greeting goal, no language detected yet) -- this
    keeps the processor a true no-op on the common early-call case rather
    than injecting an empty or placeholder block."""
    slot_summary = _format_slots(state.slots)
    goal_hint = _GOAL_HINTS.get(state.conversation_goal, "")
    language_hint = _language_hint(state)

    if not slot_summary and not goal_hint and not language_hint and not state.quoted_price:
        return ""

    lines = []
    if slot_summary:
        lines.append(f"Already collected this call: {slot_summary}. Do not re-ask for these.")
    if state.recommendations_shown:
        names = ", ".join(o["name"] for o in state.recommendations_shown)
        lines.append(f"Already recommended: {names}. Do not re-list these unless asked or something new is known.")
    if state.quoted_price:
        # Phase 4.1: a structured fact (real total, real dates, real
        # property) instead of asking the model to recall a specific number
        # from a long transcript -- same principle as recommendations_shown.
        qp = state.quoted_price
        lines.append(
            f"You already quoted ₹{qp['total']:,.0f} for {qp['property_name']} ({qp['check_in']} to "
            f"{qp['check_out']}). Do not re-quote a different number for the same property/dates unless "
            "the guest asks for a discount or something has genuinely changed."
        )
    if goal_hint:
        lines.append(f"Current objective: {goal_hint}")
    if language_hint:
        lines.append(language_hint)

    return "\n".join(lines)


class StatePromptSyncProcessor(FrameProcessor):
    """Keeps one state-summary system message in context, always up to date,
    right before the system prompt's fixed content stops mattering and the
    live conversation state starts mattering more."""

    def __init__(self, conversation_state: ConversationState):
        super().__init__()
        self._state = conversation_state
        # The exact dict object last inserted into context.messages by this
        # processor, tracked by Python object identity (see module docstring
        # for why a marker FIELD on the message itself is unsafe -- it leaks
        # into the raw provider request body). None until the first turn
        # that actually has something worth surfacing.
        self._last_message: dict | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            content = build_state_block_content(self._state)
            messages = frame.context.messages

            existing_index = next(
                (i for i, m in enumerate(messages) if m is self._last_message), None
            )

            if content:
                new_message = {"role": "system", "content": content}
                if existing_index is not None:
                    messages[existing_index] = new_message
                else:
                    # Insert right after the real system prompt (index 0) --
                    # before it would compete with/shadow GOLDEN_RULES; after
                    # any real conversation turns would look like it's part
                    # of the dialogue rather than a system-level fact.
                    messages.insert(1, new_message)
                self._last_message = new_message
            elif existing_index is not None:
                # Nothing left worth surfacing (shouldn't normally happen
                # once slots start accumulating, since they're never
                # cleared mid-call, but handled for completeness/tests).
                del messages[existing_index]
                self._last_message = None

        await self.push_frame(frame, direction)
