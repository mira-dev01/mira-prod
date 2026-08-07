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

    from app.voice.conversation_style import ConversationStyle, TurnSignal

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
    # currently speak, computed from a rolling window of the guest's OWN
    # turns (style_history below), on top of current_spoken_language's
    # single-turn, unconditionally-overwritten signal above. Deliberately a
    # SEPARATE pair of fields, not a replacement -- current_spoken_language/
    # explicit_language_preference keep driving the live TTS-language switch
    # (LanguageSyncProcessor) and the Response Validator's own checks
    # exactly as before; conversation_style is additive, read by
    # StatePromptSyncProcessor to build the LLM-facing style block. Typed
    # under TYPE_CHECKING for the same reason current_spoken_language is.
    conversation_style: "ConversationStyle | None" = None
    # Bounded (StyleEngine.update trims to its own rolling_window) list of
    # per-turn TurnSignal facts the engine needs to recompute a weighted
    # score -- owned here, not inside StyleEngine itself, so the engine
    # stays a stateless pure function (see its own docstring) and this
    # dataclass remains the single place ALL per-call state lives.
    style_history: "list[TurnSignal]" = field(default_factory=list)

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
        set by an earlier call. `None`/unset values are simply not written."""
        if value is None:
            return
        self.slots[key] = value
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
