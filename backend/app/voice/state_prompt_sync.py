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

from app.voice.conversation_quality import ConversationQuality
from app.voice.conversation_state import ConversationState
from app.voice.conversation_style import render_style_block

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
    # "closing" is handled separately by _closing_hint below -- a real,
    # committed guest (accepted a property AND was quoted a real price)
    # needs different framing from one who's still just browsing when the
    # call winds down. Kept out of this dict (rather than one static string)
    # since the right wording is derived from other ConversationState facts,
    # not the goal value alone.
}

# Phase 8 (Closing intelligence): "hard close" (guest_accepted_property_id
# AND quoted_price both set -- a real property was settled on AND a real
# price was actually quoted for it, not just recommended) vs "soft close"
# (neither, or only one) is derived entirely from facts ConversationState
# already tracks -- no new field, same "hand the model a derived fact, don't
# make it guess" discipline every other hint in this module already follows.
# Deliberately does NOT say "last chance" / "act now" / any invented scarcity
# claim -- this codebase has no real "N units left" signal to ground that in
# (see calendar_service.next_available_window, which IS a real fact, already
# surfaced directly in check_calendar's own "not available" reply text, not
# duplicated here). Urgency here means "this guest already showed real
# commitment, don't let the close undersell that," not fabricated pressure.
_HARD_CLOSE_HINT = (
    "The call is closing and the guest has already accepted a specific property and heard a real "
    "price for it -- this is a hard close: reinforce that you've noted everything down and the host "
    "will follow up soon to confirm, so the guest leaves the call confident their booking is actually "
    "moving forward, not just that you were polite. Don't reopen new topics unless the guest raises "
    "something new."
)
_SOFT_CLOSE_HINT = (
    "The call is closing and the guest has not committed to a specific property/price yet -- this is a "
    "soft close: keep the door open naturally (e.g. mention they're welcome to call back) rather than "
    "implying anything is booked or confirmed. Don't reopen new topics unless the guest raises something new."
)


def _closing_hint(state: ConversationState) -> str:
    if state.conversation_goal != "closing":
        return ""
    # Self-review fix: guest_accepted_property_id and quoted_price are both
    # sticky (never cleared, only overwritten -- see their own docstrings
    # above in this dataclass) -- a guest who accepted/was quoted for
    # Property A, then explicitly switched to browsing Property B, leaves
    # both fields stale-truthy for A even though nothing was confirmed for
    # B. Comparing quoted_price's own property_name against
    # selected_property_name (set together with guest_accepted_property_id
    # by the same lock_property call) confirms the quote actually belongs
    # to the CURRENTLY accepted property, not a stale one from earlier in
    # the call.
    if (
        state.guest_accepted_property_id
        and state.quoted_price
        and state.quoted_price.get("property_name") == state.selected_property_name
    ):
        return _HARD_CLOSE_HINT
    return _SOFT_CLOSE_HINT


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


def _format_slots(state: ConversationState) -> str:
    """Orders known slots by attention score (most emphasized/most recently
    restated first), not _SLOT_LABELS' fixed dict order -- a guest who's
    corrected their budget twice cares more about it being respected than
    one set once early on and never revisited, even though both are equally
    "known." A slot restated (genuinely changed, not backfilled -- see
    ConversationState.set_slot) 2+ times also gets an explicit annotation,
    since the LLM shouldn't have to infer emphasis from message order alone."""
    known = [(key, label) for key, label in _SLOT_LABELS.items() if key in state.slots and state.slots[key] is not None]
    known.sort(key=lambda kl: state.attention_score(f"slot:{kl[0]}"), reverse=True)
    parts = []
    for key, label in known:
        value = state.slots[key]
        if key == "budget":
            value = f"~₹{value:,.0f}"
        text = f"{label}: {value}"
        salience = state.attention.get(f"slot:{key}")
        if salience is not None and salience.count >= 2:
            text += f" (guest has restated this {salience.count}x -- weigh it heavily)"
        parts.append(text)
    return ", ".join(parts)


def _language_hint(state: ConversationState, quality: "ConversationQuality | None") -> str:
    """Renders the Conversation Style Engine's own structured block
    (app/voice/conversation_style.py's render_style_block) once
    state.conversation_style exists -- this REPLACES the earlier single-line
    passive/explicit language hint that used to read
    current_spoken_language/explicit_language_preference directly (those two
    fields are unchanged and still exist, still drive LanguageSyncProcessor's
    live TTS switch; only THIS prompt-rendering consumer now sources the
    LLM-facing text from the hysteresis-smoothed ConversationStyle instead
    of the raw single-turn signal, since a raw signal flipping every turn
    was exactly the inconsistent-style regression this engine exists to
    fix). Falls back to "" (no hint at all) before ConversationStyleProcessor
    has computed anything yet, e.g. the very first turn of a call before any
    guest speech has been transcribed.

    Response output architecture redesign: if ConversationQuality reports a
    pending style correction (StyleComplianceMonitor detected a confirmed
    language mismatch on the previous turn), this asks render_style_block
    for a more emphatic rendering of the SAME style -- this is the one
    permitted, one-directional bridge from quality back to conversational
    behavior (see conversation_quality.py's own module docstring): a
    quality signal can make ConversationStyle speak louder, but no
    validator ever writes prompt text itself. The flag is cleared
    immediately after being consumed here so the emphasis is a one-turn
    nudge, not a permanent state."""
    if state.conversation_style is None:
        return ""
    emphasized = quality is not None and quality.pending_style_correction
    if emphasized:
        quality.clear_pending_style_correction()
    return render_style_block(state.conversation_style, emphasized=emphasized)


def _negotiation_hint(state: ConversationState) -> str:
    """Phase 4F -- turns state.last_negotiation_decision (set by
    app/voice/tools.py's negotiate_rate wrapper, see
    ConversationState.record_negotiation_decision's own docstring) into a
    plain-English fact line, same "hand the model a derived fact, don't
    make it guess" principle every other hint in this module already
    follows. Deliberately states ONLY facts already computed by
    pricing_engine.py (asking price, resulting price, whether this event
    progressed, whether negotiation is exhausted) -- never a percentage,
    never a stage count phrased as "stage N of M" (guests don't think in
    stage numbers), never a fixed sentence the model must repeat verbatim.
    The model chooses its own natural phrasing from these facts, same as
    every other hint here.

    No case below is host-specific: a host with a flat (non-staged) policy
    simply never has is_staged=True, so this always falls into the first,
    simplest branch for them -- nothing about this function assumes any
    particular stage count or value exists.

    Self-review finding: exhausted=True means the STAGE LADDER has reached
    its last rung, not that counter_offer itself equals the true floor --
    pricing_engine.negotiate_rate's accepted branch sets counter_offer to
    the guest's own (unclamped) offer, which can be strictly ABOVE
    floor_price when the guest offered more than the minimum required.
    Phrasing that case as "this is the maximum you're authorized to
    offer" was confirmed reachable and false: real floor_price could be
    materially lower, and the LLM would wrongly refuse further guest
    pushback it was still authorized to grant. at_true_floor below gates
    the exhausted-specific wording on counter_offer actually being at (or
    below, accounting for float rounding) floor_price -- an accepted
    offer above the floor while exhausted now falls through to the
    ordinary progressed/non-progressed staged wording instead, which
    makes no false claim about how much room is left."""
    decision = state.last_negotiation_decision
    if not decision:
        return ""

    asking = decision["asking_price"]
    final = decision["counter_offer"]
    floor = decision["floor_price"]
    genuinely_lower = final < asking - 0.5  # tolerate float rounding, not a real discount signal
    at_true_floor = final <= floor + 0.5  # tolerate float rounding, not a real equality signal

    if not decision["is_staged"]:
        # Flat/no-staging case (every pre-Phase-4D host, and any Phase 4D+
        # host who never configured a staged ladder) -- the only fact that
        # matters is whether the resulting price is actually below asking.
        if genuinely_lower:
            return (
                f"You just offered a concession on {decision['property_name']}: the standard price is "
                f"₹{asking:,.0f}, and you brought it down to ₹{final:,.0f}. Communicate this clearly as a "
                "real concession, not just a price -- the guest should understand a discount was actually "
                "granted."
            )
        return (
            f"You just confirmed ₹{final:,.0f} for {decision['property_name']} -- no discount was applied "
            "beyond what was already quoted. Do not imply a further concession was granted."
        )

    # Staged case -- the four Phase 4D facts (progressed_this_event,
    # exhausted, stage_index, stage_count) together determine which of the
    # three distinct guest-facing situations this is, without this
    # function inventing a fourth. exhausted must ALSO be paired with
    # at_true_floor (self-review addition) -- see this function's own
    # docstring for why exhausted alone isn't sufficient to claim "no
    # further concession available."
    if decision["exhausted"] and at_true_floor:
        return (
            f"You just offered ₹{final:,.0f} for {decision['property_name']} (standard price ₹{asking:,.0f}) "
            "-- this is the maximum you're authorized to offer; there is no further concession available "
            "no matter how the guest pushes back. Communicate that this is genuinely your best price, "
            "naturally -- do not invent an additional discount."
        )
    if decision["progressed_this_event"]:
        return (
            f"The guest's pushback just earned a BETTER concession on {decision['property_name']}: standard "
            f"price is ₹{asking:,.0f}, and you can now offer ₹{final:,.0f}, an improvement on what was "
            "offered before. Make clear this is a genuinely better offer than last time, not the same one "
            "repeated."
        )
    return (
        f"The guest is still at the same authorized price for {decision['property_name']}: ₹{final:,.0f} "
        f"(standard price ₹{asking:,.0f}). This is not a new or improved concession -- if they're asking "
        "again without a genuinely different offer, you can restate this price, but do not imply anything "
        "changed or invent a further discount."
    )


def build_state_block_content(state: ConversationState, quality: "ConversationQuality | None" = None) -> str:
    """Returns "" when there's nothing worth surfacing yet (no slots known,
    still at the default greeting goal, no language detected yet) -- this
    keeps the processor a true no-op on the common early-call case rather
    than injecting an empty or placeholder block. quality is optional and
    read ONLY for the one narrow pending_style_correction bridge -- see
    _language_hint's own docstring."""
    slot_summary = _format_slots(state)
    goal_hint = _closing_hint(state) or _GOAL_HINTS.get(state.conversation_goal, "")
    language_hint = _language_hint(state, quality)
    negotiation_hint = _negotiation_hint(state)

    if not slot_summary and not goal_hint and not language_hint and not state.quoted_price and not negotiation_hint:
        return ""

    lines = []
    if slot_summary:
        lines.append(f"Already collected this call: {slot_summary}. Do not re-ask for these.")
    if state.recommendations_shown:
        names = ", ".join(o["name"] for o in state.recommendations_shown)
        lines.append(f"Already recommended: {names}. Do not re-list these unless asked or something new is known.")
    # Phase 4F: negotiation_hint SUPERSEDES the plain quoted_price hint
    # below when the most recent price event was itself a negotiation --
    # app/voice/tools.py's negotiate_rate wrapper now calls BOTH
    # record_quoted_price and record_negotiation_decision on every
    # successful negotiation (record_quoted_price closes a real, separate
    # gap: repetition_guard.py's cross-turn repeat check is keyed to
    # quoted_price specifically, so negotiated prices need to populate it
    # too), which would otherwise produce two overlapping/redundant lines
    # about the same number. state.last_negotiation_decision being
    # non-None IS the "is a negotiation still the current price fact"
    # signal -- get_pricing's own wrapper explicitly clears it
    # (ConversationState.clear_negotiation_decision) the moment a plain
    # quote supersedes it, so no inference/comparison against quoted_price
    # is needed here.
    if negotiation_hint:
        lines.append(negotiation_hint)
    elif state.quoted_price:
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

    def __init__(self, conversation_state: ConversationState, quality: ConversationQuality | None = None):
        super().__init__()
        self._state = conversation_state
        # Optional -- only needed to consume a pending style correction
        # signal (see build_state_block_content/_language_hint). None is a
        # genuine no-op, same optional-dependency pattern every other
        # processor in this codebase already uses.
        self._quality = quality
        # The exact dict object last inserted into context.messages by this
        # processor, tracked by Python object identity (see module docstring
        # for why a marker FIELD on the message itself is unsafe -- it leaks
        # into the raw provider request body). None until the first turn
        # that actually has something worth surfacing.
        self._last_message: dict | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            content = build_state_block_content(self._state, self._quality)
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
