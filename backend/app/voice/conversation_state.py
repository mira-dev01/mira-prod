"""Per-call mutable state tracked programmatically alongside the LLM's own
context, rather than relying solely on the model remembering facts across
turns (see documentation/memory-architecture-plan.md section 2 for the bug
this fixes, and documentation/agent-conversation-improvement.md Phase 1 for
the slot/goal/lifecycle fields below).

Started as just selected_property_id/selected_property_name (the property-
lock fix) -- memory-architecture-plan.md's own section 2.2 sketched
booking_stage/slots_filled/last_intent and deliberately deferred them ("can
be added back when a task actually needs them"). This is that task: real,
confirmed-live failures (a recommendation silently violating a guest count
the guest had already stated; a conversation drifting between
question/recommendation with no forward progress) need a structured "what's
already known" and "what are we trying to do right now" the model can be
handed directly, rather than re-deriving both from a growing transcript
every turn.

Every field here is populated as a side effect of which tool actually ran
and what's already known -- never a separate LLM classification call. This
is the same discipline app/services/guest_memory_service.py already uses
(no new LLM call where a deterministic derivation already exists).
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pipecat.transcriptions.language import Language

    from app.voice.conversation_style import ConversationStyle

ConversationGoal = Literal[
    "greeting",
    "collecting_dates",
    "collecting_guests",
    "collecting_location_or_purpose",
    "recommending",
    "awaiting_selection",
    "checking_availability",
    "negotiating",
    "collecting_lead_contact",
    "escalating",
    "closing",
]

# Lifecycle vocabulary cross-reference (architecture-cleanup pass, see
# docs/how-it-works.md's Technical Debt #3). Three fields, in three different
# places, each track a different partial view of "how far along is this
# booking" -- ConversationGoal (above) is the only one this task extends, not
# a new unified field: no schema change, no migration, no behavior change.
# This comment documents how the three relate so a reader doesn't have to
# rediscover it by reading three files independently:
#
#   ConversationGoal (here)         -- in-call only, discarded at hangup.
#                                       Derived by _recompute_goal() below
#                                       from which tool last ran / which
#                                       slots are still unset. Never written
#                                       by the LLM directly.
#   Lead.lead_temperature (models/lead.py) -- "hot"/"warm"/"cold". Set by the
#                                       LLM via the update_lead tool, per
#                                       LEAD_AGENT_INSTRUCTIONS step 3/6
#                                       (system_prompt.py). Persists across
#                                       the call and across future calls from
#                                       the same guest. Not validated against
#                                       ConversationGoal in code -- the two
#                                       can disagree (e.g. goal="negotiating"
#                                       with temperature still "warm").
#   Lead.status (models/lead.py)    -- "open"/"booked"/"closed" etc. Host-set
#                                       only, from the dashboard Kanban
#                                       (PATCH /leads/{id}). The voice agent
#                                       never writes this field -- there is
#                                       no code path from ConversationGoal or
#                                       lead_temperature into status.
#
# Rough correspondence (informal, not enforced): ConversationGoal values up
# through "collecting_lead_contact" correspond to Lead.status="open" with a
# temperature climbing cold->warm->hot; "escalating" corresponds to
# Lead.escalated=True being set; "closing" has no Lead.status counterpart at
# all -- a call can close without the host ever having confirmed a booking.
# A genuine shared enum spanning all three (proposed, not built here) would
# need a real migration and a decision on backfilling existing Lead rows --
# out of scope for this pass; see docs/how-it-works.md's Refactoring Plan.
ClosingState = Literal["open", "farewell_pending", "closed"]

# Attention/salience tracking -- how much weight a fact deserves isn't just
# "is it known" (slots already answers that) but "how much has the guest
# actually emphasized it" -- repeated across turns, or stated recently, vs.
# mentioned once early on and never revisited. Repetition + recency ONLY, by
# explicit product decision: no emphasis-word/sentiment text classification,
# same "derived from tool-call activity, never a new LLM/NLU pass" discipline
# every other field on this dataclass already follows (see module docstring).
# Half-life decay, not linear -- needs to keep behaving sensibly for a call
# of ANY length, not just a fixed-size window (contrast with
# conversation_style.py's _RECENCY_WEIGHTS, a fixed 6-entry table -- fine for
# StyleEngine's fixed rolling window, but this needs a continuous curve
# since a call could run 3 turns or 30).
_DEFAULT_ATTENTION_HALF_LIFE = 6


@dataclass(frozen=True)
class NegotiationEvent:
    """One negotiate_rate invocation, recorded for the lifetime of the
    current negotiation context only (Phase 4D, implementing Phase 4C
    Section F's minimum REQUIRED FOR DECISION field set -- see
    documentation design docs "Phase 4C: Negotiation Semantics Contract").

    guest_offer=None means the guest asked Mira to name a price outright
    (pricing_engine.negotiate_rate's existing guest_offer=None branch) --
    distinct from a numeric offer, and NEVER counted as a progressing
    offer by resolve_stage_index (app/services/negotiation_policy.py)
    regardless of how many stages a host's policy has, since it carries no
    number to compare against. property_id is kept alongside each event
    (not just once on the call) so a future context-invalidation check can
    always tell, from the event list alone, whether every event still
    belongs to the same negotiation -- see reset_negotiation_context below,
    which is today's actual mechanism for that invalidation."""

    guest_offer: float | None
    property_id: str


@dataclass
class Salience:
    """One key's raw attention bookkeeping. Generic over what `key` means
    (a "slot:<name>" or "amenity:<canonical>" composite string, see
    ConversationState.touch_attention) -- this stays a small, reusable
    primitive rather than a bespoke mechanism per caller."""

    count: int = 0
    last_turn: int = 0

    def score(self, current_turn: int, half_life: int = _DEFAULT_ATTENTION_HALF_LIFE) -> float:
        """Repetition count decayed by how many turns ago it was last
        touched -- a fact mentioned 3 times long ago can still rank below
        one mentioned twice recently. half_life <= 0 degrades to "only the
        current turn counts at full weight, everything older is zero",
        rather than dividing by zero."""
        turns_ago = max(0, current_turn - self.last_turn)
        if half_life <= 0:
            recency = 1.0 if turns_ago == 0 else 0.0
        else:
            recency = 0.5 ** (turns_ago / half_life)
        return self.count * recency

# Priority order for deriving a goal from which slots are still unset, absent
# a more specific tool-driven signal -- mirrors LEAD_AGENT_INSTRUCTIONS step 2
# (system_prompt.py), which already asks for dates, then guests, then
# location/purpose in this order. This is a restatement of existing prompt
# logic, not a new policy invented for this dataclass.
_SLOT_GOAL_PRIORITY: list[tuple[str, ConversationGoal]] = [
    ("check_in", "collecting_dates"),
    ("check_out", "collecting_dates"),
    ("num_guests", "collecting_guests"),
    ("preferred_location", "collecting_location_or_purpose"),
    ("purpose_of_stay", "collecting_location_or_purpose"),
]


@dataclass
class ConversationState:
    selected_property_id: str | None = None
    selected_property_name: str | None = None

    # Phase 1.1 -- populated from tool-call arguments as they're actually
    # supplied (check_calendar/get_pricing/negotiate_rate/update_lead/
    # recommend_properties), never re-derived by a separate NLU pass. Only
    # ever updated per-field (see set_slot) so a later call that omits a
    # field already known never clobbers it.
    slots: dict[str, Any] = field(default_factory=dict)

    # Set whenever recommend_properties actually returns real options, so
    # later turns/guards can check "has this already been shown" without
    # re-parsing history. Holds PropertyCard-shaped dicts (name/price/guests),
    # not full ORM/dataclass objects, to keep this module free of a
    # dependency on the property/retrieval package.
    recommendations_shown: list[dict[str, Any]] = field(default_factory=list)

    # Set when a tool call implies the guest has settled on one of the
    # recommended properties (check_calendar/get_pricing/negotiate_rate
    # called with a property_id matching one already in recommendations_shown)
    # -- same "the required tool-arg already is the signal" principle
    # selected_property_id itself already uses.
    guest_accepted_property_id: str | None = None

    # Set the moment escalate_to_host's wrapper fires. Never reset false
    # again mid-call -- an escalated call stays escalated for the rest of
    # the call, same as GOLDEN_RULES' own "say the escalation phrase only
    # once per call" rule.
    escalated: bool = False

    # Owned end-to-end by Phase 5 (closing lifecycle) -- declared here so
    # this dataclass ships its complete shape once. "farewell_pending" is
    # set the same turn end_call/decline_irrelevant_call fires; "closed"
    # once the hangup actually happens; reset to "open" if the guest speaks
    # again before the hangup completes (silence_watchdog's own cancellation
    # path already detects this -- see Phase 5).
    closing_state: ClosingState = "open"

    # Phase 1.5 -- what Mira is currently trying to achieve, distinct from
    # what's already known (slots above). Derived, never LLM-classified.
    conversation_goal: ConversationGoal = "greeting"

    # Phase 3.1 (documentation/agent-conversation-improvement.md) -- the
    # guest's currently-detected SPOKEN language, written by
    # LanguageSyncProcessor (app/voice/language_sync.py) on every
    # TranscriptionFrame -- the same signal that already drives the TTS
    # voice switch, now also fed back here so the LLM's own reply-language
    # choice can be told directly instead of having to infer it from
    # re-reading the transcript. Typed as "Language | None" only under
    # TYPE_CHECKING (see import above) to keep this lightweight dataclass
    # module free of a hard runtime dependency on pipecat's enum.
    current_spoken_language: "Language | None" = None

    # Phase 3.3 -- a guest's EXPLICIT, stated language request ("can you
    # speak Hindi?", "English mein baat karo please"), distinct from
    # current_spoken_language's passive per-turn detection above. A
    # stronger, more deliberate signal that should override passive
    # mirroring for the rest of the call once set. No clean tool-call
    # signal exists for this (a guest can state it in plain conversation
    # with no tool involved), so this field is prompt-derived rather than
    # tool-derived -- see GOLDEN_RULES' explicit-language-request clause.
    explicit_language_preference: "Language | None" = None

    # Conversation Style Engine (app/voice/conversation_style.py) -- a
    # higher-level, hysteresis-smoothed judgment of how Mira should
    # currently speak, on top of current_spoken_language's single-turn,
    # unconditionally-overwritten signal above. Deliberately a SEPARATE pair
    # of fields, not a replacement -- current_spoken_language/
    # explicit_language_preference keep driving the live TTS-language switch
    # (LanguageSyncProcessor) exactly as before; conversation_style is
    # additive, read by StatePromptSyncProcessor to build the LLM-facing
    # style block. This is a REFERENCE ONLY (the current computed
    # ConversationStyle snapshot) -- the engine's own rolling-window working
    # memory (style_history) lives inside StyleEngine itself (see
    # conversation_style.py), never here: ConversationState holds
    # conversation facts, not another module's internal mechanism. Typed
    # under TYPE_CHECKING for the same reason current_spoken_language is.
    conversation_style: "ConversationStyle | None" = None

    # Phase 4.1 (documentation/agent-conversation-improvement.md) -- the
    # real total get_pricing last quoted, keyed to the property/dates it was
    # quoted for. Lets the prompt say "you already quoted ₹X for these
    # dates" instead of relying on the model to recall a specific number
    # from a long transcript -- the same structural-fact-over-text-recall
    # principle recommendations_shown already applies. Reset (never
    # correctly "cleared", only overwritten) whenever get_pricing is called
    # again -- a later quote (different dates, or the same dates re-quoted
    # with a discount applied) is always the current one to surface.
    quoted_price: dict[str, Any] | None = None

    # Phase 4F (conversation-level negotiation integration) -- the
    # structured facts from negotiate_rate's most recent NegotiationResult,
    # same "hand the model a derived fact, don't make it guess" principle
    # quoted_price above already applies, extended to the Phase 4D fields
    # (is_staged/stage_index/stage_count/progressed_this_event/exhausted)
    # that existed on NegotiationResult since Phase 4D but were never
    # surfaced anywhere past pricing_engine.negotiate_rate's own return
    # value -- tool_handlers.handle_negotiate_rate discarded all of them,
    # returning only the bare message string (confirmed: no caller read
    # anything but .message before this phase). Without this, the LLM had
    # no way to know whether a price change was a genuine new concession,
    # a repeat of what was already offered, or the final authorized value --
    # exactly the "Mira changes the price without clearly communicating a
    # discount was granted" problem this phase's own brief names. Always
    # overwritten (never merged), matching quoted_price's own discipline --
    # the most recent negotiation decision is always the one to reference.
    last_negotiation_decision: dict[str, Any] | None = None

    # Attention/salience -- see Salience above. turn_index is a single
    # shared per-call counter (advanced by ConversationStyleProcessor on
    # every real guest TranscriptionFrame, the same "one real utterance = one
    # turn" signal that module's own hysteresis logic already relies on --
    # see conversation_style.py). attention holds one Salience per tracked
    # key, written only via touch_attention, never mutated directly.
    turn_index: int = 0
    attention: dict[str, Salience] = field(default_factory=dict)

    # Phase 4D -- call-level negotiation event history (Phase 4C Section F:
    # "Call-level (starts at call connect, discarded at hangup)"). Holds
    # only NegotiationEvent entries for the property/dates currently being
    # negotiated -- reset_negotiation_context (below) clears this whenever
    # the guest changes property/dates/guest count, per the ratified Phase
    # 4C decision that negotiation STATE resets on those changes while
    # conversational context (everything else on this dataclass, and the
    # LLM's own transcript) is left untouched. Deliberately NOT a stage
    # counter -- see negotiation_policy.resolve_stage_index, which derives
    # the current stage from this list every time rather than trusting a
    # separately-maintained integer (Phase 4C Section G's own reasoning for
    # why: a stored counter can drift from the event log it's supposed to
    # summarize; a derived value structurally cannot).
    negotiation_events: list[NegotiationEvent] = field(default_factory=list)

    def record_negotiation_event(self, guest_offer: float | None, property_id: str) -> None:
        """Called once per negotiate_rate invocation, AFTER pricing_engine
        has already resolved this turn's decision (see app/voice/tools.py's
        wrapper) -- appends, never overwrites, since stage derivation needs
        the full history, not just the latest value.

        The property-change guard below is a defensive backstop, not the
        primary invalidation mechanism -- the wrapper itself now detects a
        property change BEFORE calling into pricing_engine (comparing the
        incoming property_id against negotiation_events[-1] itself) and
        calls reset_negotiation_context() first, so by the time this method
        runs, property_id should already match. Self-review fix: an earlier
        version relied on THIS method as the only property-change reset,
        which only ever ran after pricing_engine.negotiate_rate had already
        resolved the turn against the stale, wrong-property history -- the
        first negotiate_rate call after a property switch was silently
        evaluated against the OLD property's progress. Kept here anyway as
        a fail-safe for any future caller that appends directly without
        going through the wrapper's own pre-check."""
        if self.negotiation_events and self.negotiation_events[-1].property_id != property_id:
            self.negotiation_events = []
        self.negotiation_events.append(NegotiationEvent(guest_offer=guest_offer, property_id=property_id))

    def reset_negotiation_context(self) -> None:
        """Discards negotiation event history and the last negotiation
        decision fact -- called when the guest changes property, dates, or
        guest count (app/voice/tools.py's negotiate_rate wrapper, comparing
        the incoming values against state's existing ones before this call
        overwrites them). Per the ratified Phase 4C decision (Decisions Log
        item 4): negotiation STAGE/PRICING state resets on a context
        change, but conversational context is retained -- this method only
        ever touches negotiation_events and last_negotiation_decision,
        never slots/recommendations_shown/conversation_goal/etc., so the
        LLM can still naturally reference what was already discussed.

        Phase 4F addition: last_negotiation_decision must be cleared here
        too, not just negotiation_events -- once the property/dates/guest
        count the guest is now discussing has changed, the PREVIOUS
        decision's concession no longer describes anything true about the
        new context (e.g. "you can offer ₹X for Property A" would be a
        stale, misleading fact once the guest has moved on to Property B).
        Leaving it stale would have contradicted this method's own
        "invalidate negotiation state on context change" purpose for
        exactly the one negotiation-shaped field this dataclass gained
        after Phase 4C's decision was ratified."""
        self.negotiation_events = []
        self.last_negotiation_decision = None

    def touch_attention(self, key: str) -> None:
        """Records one more mention of `key` at the current turn. Callers:
        set_slot (below, automatic for every scalar slot write) and
        app/voice/tools.py's recommend_properties wrapper (explicit, for
        amenities -- these accumulate as a list rather than overwrite, so
        they can't piggyback on set_slot's per-field semantics)."""
        entry = self.attention.setdefault(key, Salience())
        entry.count += 1
        entry.last_turn = self.turn_index

    def attention_score(self, key: str, half_life: int = _DEFAULT_ATTENTION_HALF_LIFE) -> float:
        entry = self.attention.get(key)
        if entry is None:
            return 0.0
        return entry.score(self.turn_index, half_life)

    def advance_turn(self) -> None:
        self.turn_index += 1

    def lock_property(self, property_id: str | None, property_name: str | None = None) -> None:
        if not property_id:
            return
        self.selected_property_id = property_id
        if property_name:
            self.selected_property_name = property_name
        # A tool call naming a specific property is also evidence the guest
        # has accepted/settled on it, if it was one of the ones just shown --
        # same reasoning as guest_accepted_property_id's own docstring above.
        if any(str(o.get("property_id")) == str(property_id) for o in self.recommendations_shown):
            self.guest_accepted_property_id = property_id
        self._recompute_goal(after_tool="lock_property")

    def set_slot(self, key: str, value: Any) -> None:
        """Set a single slot field. Never call this with a blind dict merge --
        a tool call that only supplies `phone` must never clobber a `num_guests`
        set by an earlier call. `None`/unset values are simply not written.

        Only touches attention when the value actually CHANGES (including
        the first time it's set). Several callers (recommend_properties'
        wrapper in app/voice/tools.py, most notably) call set_slot every
        turn with a BACKFILLED value pulled from state.slots itself when the
        model omitted the field that call -- that's bookkeeping to keep the
        slot alive, not the guest restating anything, and must not inflate
        the repetition signal. A value that genuinely changes (first set, or
        a real correction like "actually make that 6 guests") always is real
        signal, backfill-of-the-unchanged-value never is."""
        if value is None:
            return
        changed = self.slots.get(key) != value
        self.slots[key] = value
        if changed:
            self.touch_attention(f"slot:{key}")
        self._recompute_goal(after_tool="set_slot")

    def record_recommendations(self, options: list[dict[str, Any]]) -> None:
        self.recommendations_shown = options
        self._recompute_goal(after_tool="recommend_properties")

    # Recommendation conversations ("Phase X"): "something cheaper"/"larger"
    # is a RELATIVE instruction -- the guest is comparing against what was
    # ALREADY shown, never naming a new absolute number themselves. The LLM
    # only has to recognize that relative intent (RecommendPropertiesArgs.
    # cheaper_than_shown/larger_than_shown); these two resolve it into a
    # real threshold derived from recommendations_shown, the same "hand it
    # a fact, don't make it guess" discipline _today_anchor() and
    # comparison_notes already use elsewhere in this codebase. Both return
    # None when there's nothing yet to be relative TO (recommendations_shown
    # empty) -- the tool wrapper (app/voice/tools.py) falls back to treating
    # the call as a normal, non-relative search in that case rather than
    # erroring, since a guest can technically say "something cheaper" as
    # their very first utterance with nothing shown yet to compare against.
    def resolve_cheaper_budget(self) -> float | None:
        """A real ceiling BELOW the cheapest property already shown --
        cheapest, not average, since "cheaper" means cheaper than the best
        price already seen, not cheaper than the middle of the pack. 20%
        below the cheapest shown, not 10% -- filter_builder.build_base_filters'
        own budget filter re-adds 15% headroom on top of whatever this
        returns (`base_price <= budget * 1.15`), so the discount here must
        net BELOW 1.0 after that multiply or the cheapest-shown property
        (or something even pricier) can still pass the filter and re-match
        itself. 0.8 * 1.15 = 0.92 -- genuinely excludes the cheapest shown."""
        prices = [o["price"] for o in self.recommendations_shown if o.get("price") is not None]
        if not prices:
            return None
        return min(prices) * 0.8

    def resolve_larger_num_guests(self) -> int | None:
        """A real floor ABOVE the largest capacity already shown -- largest,
        not average, for the same reason resolve_cheaper_budget anchors on
        the cheapest: "larger" means larger than the biggest one already
        seen, not larger than the middle of the pack. +1 is enough to
        exclude every already-shown property from apply_guest_count_filter's
        own >= check while still finding the very next size up, not
        over-shooting to a much bigger property than the guest likely
        wants."""
        guest_counts = [o["guests"] for o in self.recommendations_shown if o.get("guests") is not None]
        if not guest_counts:
            return None
        return max(guest_counts) + 1

    def record_quoted_price(self, property_name: str, check_in: str, check_out: str, total: float) -> None:
        """Phase 4.1 -- always overwrites, never merges: a later quote (a
        discount applied on request, or different dates) is always the
        current one the model should reference, not the first one given."""
        self.quoted_price = {
            "property_name": property_name,
            "check_in": check_in,
            "check_out": check_out,
            "total": total,
        }

    def record_negotiation_decision(
        self,
        property_name: str,
        asking_price: float,
        counter_offer: float,
        accepted: bool,
        is_staged: bool,
        stage_index: int | None,
        stage_count: int | None,
        progressed_this_event: bool,
        exhausted: bool,
        floor_price: float,
    ) -> None:
        """Phase 4F -- always overwrites, never merges, same discipline as
        record_quoted_price above: the most recent negotiation decision is
        always the one the model should reference. Called once per
        successful negotiate_rate invocation (app/voice/tools.py's wrapper,
        mirroring exactly how that same wrapper already calls
        record_quoted_price for get_pricing) -- NOT called on a refused/
        error path (property not found, negotiation_allowed=False's own
        message already says enough on its own, non-positive-price guard),
        matching handle_negotiate_rate's own on_priced callback contract
        (only fires on a real successful negotiation).

        No field here is a percentage or a host-specific value -- every
        number is this call's own real asking_price/counter_offer/
        floor_price, and is_staged/stage_index/stage_count/
        progressed_this_event/exhausted are copied straight from
        NegotiationResult (pricing_engine.py), never invented or
        re-derived here. A host with no staged policy (the pre-Phase-4D/
        flat-only case) simply has is_staged=False, stage_index=None,
        stage_count=None -- this method makes no assumption about whether
        staging is in play.

        floor_price (self-review addition): the true policy floor this
        turn, which is NOT always equal to counter_offer -- on the
        accepted branch of pricing_engine.negotiate_rate, counter_offer is
        the guest's own (unclamped) offer, which can legitimately be
        ABOVE floor_price when the guest offered more than the minimum
        required. Needed so _negotiation_hint (state_prompt_sync.py) can
        tell "exhausted AND genuinely at the floor" apart from "exhausted
        AND the guest happened to offer/accept something above the floor"
        -- see that function's own docstring for the bug this closes."""
        self.last_negotiation_decision = {
            "property_name": property_name,
            "asking_price": asking_price,
            "counter_offer": counter_offer,
            "accepted": accepted,
            "is_staged": is_staged,
            "stage_index": stage_index,
            "stage_count": stage_count,
            "progressed_this_event": progressed_this_event,
            "exhausted": exhausted,
            "floor_price": floor_price,
        }

    def clear_negotiation_decision(self) -> None:
        """Phase 4F -- called by get_pricing's wrapper (app/voice/tools.py)
        whenever a plain (non-negotiated) quote is produced, so
        last_negotiation_decision can never outlive its own relevance.
        Self-review-driven design: comparing quoted_price's total against
        last_negotiation_decision's counter_offer to infer "is the
        negotiation still the current price fact" would have a real, if
        rare, false-positive edge case (a fresh flat quote that happens to
        equal a previously negotiated total by coincidence) -- explicitly
        clearing the fact at its own invalidation point (a new,
        non-negotiated quote was just given) is unambiguous and needs no
        inference. Mirrors reset_negotiation_context's own "invalidate at
        the source, don't infer staleness later" discipline."""
        self.last_negotiation_decision = None

    def mark_checking_availability(self) -> None:
        if self.escalated or self.closing_state != "open":
            return
        self.conversation_goal = "checking_availability"

    def mark_negotiating(self) -> None:
        if self.escalated or self.closing_state != "open":
            return
        self.conversation_goal = "negotiating"

    def mark_escalated(self) -> None:
        self.escalated = True
        self.conversation_goal = "escalating"

    def mark_farewell_pending(self) -> None:
        """Phase 5 (documentation/agent-conversation-improvement.md) --
        called by silence_watchdog.request_end_after_current_turn() the same
        turn end_call/decline_irrelevant_call fires, mirroring exactly how
        mark_escalated is "a tool call is the signal," no new classifier."""
        self.closing_state = "farewell_pending"
        self.conversation_goal = "closing"

    def mark_reopened(self) -> None:
        """Called by silence_watchdog when a pending close gets cancelled --
        either PrematureEndCallGuardProcessor's same-turn-question catch, or
        the guest genuinely speaking again before the hangup completes. Both
        are a real, explicit sign the call isn't actually over. Only ever
        called from farewell_pending (silence_watchdog itself guards this --
        see its own cancel paths), so conversation_goal is recomputed from
        current slots/state rather than staying stuck on "closing"."""
        self.closing_state = "open"
        self._recompute_goal(after_tool="reopened")

    def mark_closed(self) -> None:
        """Called by silence_watchdog the moment it actually pushes
        EndWorkerFrame -- the call is disconnecting, nothing downstream
        reads conversation_goal/closing_state again, but this keeps the
        state's own invariant (closing_state reflects reality) honest."""
        self.closing_state = "closed"

    def _recompute_goal(self, after_tool: str) -> None:
        """Derive conversation_goal from the strongest available signal.
        Tool-driven signals (an escalation/closing already in progress) take
        priority over slot-derived guesses -- those are handled by their own
        explicit setters (mark_escalated, closing_state's own owner in Phase
        5) and are never overwritten here. This only fires for the
        recommend/lock/slot paths, deriving a sensible next-step goal from
        what's still missing, same priority order LEAD_AGENT_INSTRUCTIONS
        step 2 already uses in prose."""
        if self.escalated or self.closing_state != "open":
            return
        if after_tool == "lock_property":
            self.conversation_goal = "awaiting_selection" if not self.guest_accepted_property_id else "checking_availability"
            return
        if after_tool == "recommend_properties":
            self.conversation_goal = "awaiting_selection"
            return
        for key, goal in _SLOT_GOAL_PRIORITY:
            if key not in self.slots:
                self.conversation_goal = goal
                return
        # Every core slot known and no property locked/recommended yet --
        # enough is known to recommend.
        if not self.selected_property_id and not self.recommendations_shown:
            self.conversation_goal = "recommending"
