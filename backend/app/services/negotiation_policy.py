"""Pure policy-evaluation layer for host-authored negotiation/pricing rules.

Phase 4 (generalized policy-driven negotiation architecture). Extracts
resolution logic that previously lived inline inside
pricing_engine._get_host_negotiation_policy/negotiate_rate into its own,
independently testable module -- same inputs, same outputs, zero behavior
change for any existing host/property/guest combination. This module is the
"policy evaluation engine" the Phase 4 brief asks for (Step 3): it answers
"which approved rules apply, and what do they authorize" as a pure function
of already-fetched NegotiationRule rows + guest/booking facts. It does not
query the database, does not call the LLM, and does not generate
conversational text -- see pricing_engine.py for the I/O and
tool_handlers.py/app/prompts for the conversation layer.

Domain-model mapping (Phase 4 brief, Step 1) -- concept -> actual repository
representation, not a new parallel model:

    Policy            -> NegotiationRule row (app/models/negotiation_rule.py)
    Condition         -> NegotiationRule.condition (JSONB) + rule_type itself
                         (rule_type IS part of the condition: it selects
                         which trigger/eligibility check applies)
    Action            -> NegotiationRule.discount_percent (the only action
                         shape the current schema supports: "allow this much
                         of a discount"). There is no separate action-type
                         column -- rule_type doubles as the action selector.
    Scope             -> NegotiationRule.property_ids (empty = host-wide for
                         discount_* trigger types; non-empty = these specific
                         properties for stay-pricing types -- see the model's
                         own docstring for why the empty-list meaning differs
                         by rule_type)
    Priority          -> NOT a first-class concept in the current schema.
                         The only existing precedence rule is implicit and
                         undocumented: when multiple approved rules of the
                         same effective type apply, pricing_engine's
                         pre-Phase-4 code took max(). This module makes that
                         EXISTING behavior explicit and named
                         (resolve_rule_type_to_best_value, "most generous wins") rather than
                         inventing a new precedence scheme -- see this
                         module's own docstring section below on what is and
                         isn't a product decision this phase can make.
    Negotiation state -> app/voice/conversation_state.py's ConversationState;
                         UNCHANGED by this module (see Phase 4 final report,
                         "Generalization Gaps" -- no offer-history/attempt-
                         count fields exist, and none are added here, since
                         the product semantics of "an attempt" are undefined
                         -- a genuine hard-stop, not solved by this module).
    Negotiation event -> the LLM-supplied negotiate_rate arguments
                         (guest_offer, guest_loyalty) -- the only structured
                         "what happened this turn" signal that reaches the
                         backend today. This module does not add new event
                         types.
    Decision          -> ResolvedDiscount (below) -- authorized percent,
                         which rule(s) produced it, and why. Distinct from
                         pricing_engine.NegotiationResult, which additionally
                         carries the PRICE arithmetic (asking price, floor,
                         counter-offer, guest-facing message) -- this module
                         only ever resolves the discount PERCENT a policy
                         authorizes; negotiate_rate still owns turning that
                         into rupee amounts and guest-facing prose.
    Pricing result    -> pricing_engine.PriceBreakdown / NegotiationResult,
                         unchanged by this module.

What this module deliberately does NOT do (see Phase 4 final report for the
full reasoning and the specific hard-stop each one maps to):

- Does not represent or evaluate multi-tier/progressive discount ladders
  (e.g. "3% then 5% then 7%") -- NegotiationRule has exactly one
  discount_percent per row; there is no ordered-list-of-tiers condition
  shape anywhere in the schema or the AI Training dashboard UI. Building
  that would mean inventing a new schema/UI this phase does not have
  product sign-off for.
- Does not introduce negotiation "attempt" or "pushback count" tracking --
  no product definition of what constitutes an attempt exists yet (a new
  guest_offer value? a repeated negotiate_rate call? an LLM-detected
  pushback phrase?).
- Does not invent new cross-rule-type precedence beyond the existing
  max()-wins behavior it makes explicit. If two DIFFERENT rule types could
  ever authorize conflicting, non-additive actions for the same guest, this
  module has no opinion beyond "each resolves independently, then the
  caller composes them the same way pricing_engine.negotiate_rate already
  does" -- because no such conflicting-action rule type exists in the
  current schema (discount_no_ask/discount_guest_requests/
  discount_repeat_guest/custom are all "authorize up to N% off", never
  mutually exclusive actions).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.negotiation_rule import NegotiationRule

if TYPE_CHECKING:
    from app.voice.conversation_state import NegotiationEvent

# The three host-wide "when should a discount be offered at all" triggers
# (formerly HostDiscountRule.trigger_type) are always host-wide regardless of
# property_ids -- see NegotiationRule's own docstring. The equivalent set
# already exists as app.schemas.tool.DISCOUNT_TRIGGER_RULE_TYPES; not
# redefined here to avoid two names for the same constant drifting apart.


@dataclass(frozen=True)
class GuestNegotiationContext:
    """The guest/booking facts a policy decision can depend on -- the
    "negotiation event" half of the domain-model mapping above. Deliberately
    narrow: only the facts the CURRENT rule_type set actually conditions on
    (repeat-guest eligibility). Extending this to more conditions (dates,
    stay duration, num_guests) is real future work, not invented here
    speculatively -- see minimum_stay_nights_violation/
    _length_of_stay_discount_percent in pricing_engine.py, which already
    evaluate their own condition shapes directly against booking facts and
    are NOT folded into this module, since their action shape (a nights
    floor / a length-of-stay discount) is evaluated at a different call site
    (check_calendar/get_pricing) than guest-initiated negotiation
    (negotiate_rate) -- see this module's own "what this does NOT do"
    section for why unifying those is out of scope for this phase."""

    is_repeat_guest: bool


@dataclass(frozen=True)
class ResolvedDiscount:
    """The policy engine's structured decision (Phase 4 brief, Step 8;
    extended Phase 4D per Phase 4C Section M's Decision contract) --
    NOT a sentence, NOT a price. Just: is a discount authorized, how much,
    and which rule(s) produced that answer. pricing_engine.negotiate_rate
    turns this into rupee amounts; the conversation layer (tool_handlers.py/
    the LLM) turns THAT into guest-facing prose -- this dataclass crosses
    neither boundary itself.

    stage_index/is_staged/progressed_this_event (Phase 4D additions) are
    None/False for every resolution that came from a flat (non-staged)
    rule -- including every resolution that existed before this phase --
    so nothing about the pre-Phase-4D fields' meaning changes. A future
    conversation layer can distinguish "flat policy, single value" from
    "staged policy, currently at stage N" purely from is_staged/stage_index
    without needing to separately query NegotiationRule.stages itself."""

    percent: float
    source_rule_ids: tuple[uuid.UUID, ...]
    reason: str  # one of the REASON_* constants below -- a stable, structured
    # tag, not prose, so a future conversation layer can key off it
    # without string-matching a message the business layer generates.
    is_staged: bool = False
    stage_index: int | None = None
    progressed_this_event: bool = False
    stage_count: int | None = None


REASON_REPEAT_GUEST = "repeat_guest_policy"
REASON_GUEST_REQUESTS = "guest_requests_policy"
REASON_CUSTOM = "custom_property_policy"

# Phase 4D: staging is additive to the reason taxonomy above, not a
# replacement -- REASON_REPEAT_GUEST/REASON_GUEST_REQUESTS/REASON_CUSTOM
# describe WHICH rule_type authorized a discount, orthogonal to whether
# that rule happens to be staged or flat. No new REASON_* constant is
# needed for "staged" itself; ResolvedDiscount.stage_index (below) already
# carries that distinction structurally.


def resolve_rule_type_to_best_value(
    rules: list[NegotiationRule], rule_type: str
) -> tuple[float, tuple[uuid.UUID, ...]] | None:
    """Among approved rules of ONE rule_type, resolves to the single
    "most generous wins" value -- the existing, real, deployed precedence
    this codebase already used inline (pricing_engine.py's pre-Phase-4
    _get_host_negotiation_policy, `guest_requests_percent = percent if ...
    else max(...)`). Made explicit and named here, not changed: if a host
    has two approved discount_guest_requests rules (5% and 7%), the
    resolved value is still 7% -- see the Phase 4 final report's
    "Generalization Gaps" section for why this specific behavior (rather
    than, say, a host-chosen priority order) is a genuine unresolved
    product decision this phase reports rather than silently changes.

    source_rule_ids includes every rule that was AT the winning value (not
    just the first one found) so a future conversation/audit layer can see
    when a resolution was ambiguous (two rules approved at the same percent)
    without this module guessing which one is "the real" source."""
    matching = [r for r in rules if r.rule_type == rule_type and r.discount_percent is not None]
    if not matching:
        return None
    best_percent = max(float(r.discount_percent) for r in matching)
    winners = tuple(r.id for r in matching if float(r.discount_percent) == best_percent)
    return best_percent, winners


def resolve_stage_index(events: list["NegotiationEvent"], stage_count: int) -> int:
    """Derives the CURRENT stage index from a call's negotiation event
    history -- Phase 4C Section G's own conceptual function, implemented
    here rather than as a separately-stored counter, for exactly the reason
    that section gives: a stored counter can silently drift from the event
    log it's supposed to summarize; a value derived fresh from the log
    every time structurally cannot. No stage count, value, or host is
    assumed here -- `stage_count` is whatever the CALLER'S resolved policy
    says it is (see resolve_rule_type_to_best_staged_or_flat_value below),
    entirely host-configured.

    Per the Phase 4C ratified progression rule (Decisions Log item 1, and
    Section D "Option C", now a confirmed product decision): only a
    strictly-higher-than-every-prior-numeric-offer event counts as
    progression. guest_offer=None events (Phase 4C Section E/S.1.6) never
    count -- they carry no number to compare, so they structurally cannot
    be "strictly higher" than anything. A first numeric offer (no prior
    events at all) evaluates against stage 0 and does NOT itself select a
    later stage no matter how large it is -- this function only ever
    counts PRIOR progressions; the very first event always lands at index
    0 before any progression has happened.

    Returns an index clamped to [0, stage_count - 1] (or 0 if
    stage_count <= 0, i.e. no stages configured at all) -- never an
    out-of-range index a caller would have to separately guard against."""
    if stage_count <= 0:
        return 0
    highest_seen: float | None = None
    stage = 0
    for event in events:
        if event.guest_offer is None:
            continue
        if highest_seen is None or event.guest_offer > highest_seen:
            if highest_seen is not None and stage < stage_count - 1:
                stage += 1
            highest_seen = event.guest_offer
    return stage


def resolve_staged_value(rule: NegotiationRule, stage_index: int) -> float | None:
    """Reads one rule's stages list at a clamped index. `stages` is a plain
    JSONB list of {"order": int, "value": float} dicts (app/models/
    negotiation_rule.py) -- arbitrary length, arbitrary values, entirely
    host-configured; no code here assumes a specific count or value.
    Returns None for a rule with no stages configured (stages is None or
    empty), so callers fall back to the flat discount_percent path exactly
    as before this phase. Malformed stage entries (missing/non-numeric
    "value") are skipped, same fail-closed discipline
    pricing_engine._condition_number already applies to condition JSONB --
    a host-editable JSONB column must never let a malformed value reach a
    live pricing calculation."""
    if not rule.stages:
        return None
    ordered = sorted(
        (s for s in rule.stages if isinstance(s, dict) and isinstance(s.get("order"), int)),
        key=lambda s: s["order"],
    )
    valid = [s for s in ordered if isinstance(s.get("value"), (int, float)) and not isinstance(s.get("value"), bool)]
    if not valid:
        return None
    clamped_index = max(0, min(stage_index, len(valid) - 1))
    return float(valid[clamped_index]["value"])


def resolve_rule_type_to_best_staged_or_flat_value(
    rules: list[NegotiationRule], rule_type: str, stage_index: int
) -> tuple[float, tuple[uuid.UUID, ...], bool, int | None] | None:
    """The staged-aware generalization of resolve_rule_type_to_best_value
    above. Per the ratified Phase 4C decision (Decisions Log item 3):
    for the same rule_type, a STAGED approved rule supersedes a FLAT
    approved rule -- never merged, never max()'d together (the two are
    structurally different action shapes, a scalar vs. an ordered list;
    Phase 4C Section I explicitly rejects merging them). Concretely: if
    ANY approved rule of this rule_type has stages configured, only staged
    rules are considered (evaluated at stage_index, then "most generous
    wins" exactly as resolve_rule_type_to_best_value already does for the
    flat case); flat-only rules of the same type are ignored entirely in
    that case. If NO rule of this type has stages configured, this
    function's behavior is IDENTICAL to resolve_rule_type_to_best_value
    (same matching set, same max(), same winner-id semantics) -- this is
    what makes every existing flat-only host's behavior byte-identical
    after this phase, confirmed by test_case_* in
    tests/test_negotiation_policy.py continuing to pass unchanged.

    Returns (percent, source_rule_ids, is_staged, stage_count) or None."""
    staged_candidates = [r for r in rules if r.rule_type == rule_type and r.stages]
    if staged_candidates:
        resolved_per_rule = []
        for rule in staged_candidates:
            value = resolve_staged_value(rule, stage_index)
            if value is not None:
                resolved_per_rule.append((rule, value))
        if not resolved_per_rule:
            return None
        best_percent = max(value for _, value in resolved_per_rule)
        winners = tuple(rule.id for rule, value in resolved_per_rule if value == best_percent)
        # stage_count reported is the winning rule's own configured length --
        # if multiple staged rules of the same type tie at the winning
        # value with DIFFERENT stage counts, the first winner's count is
        # reported (an inherent ambiguity of two competing ladders tying,
        # not something this function invents an opinion about beyond
        # picking one consistently -- see Phase 4C Section J, "multiple
        # staged policies for the same rule_type" is explicitly a
        # PRODUCT DECISION REQUIRED case this function does not resolve
        # beyond value-level "most generous wins").
        winning_rule = next(rule for rule, value in resolved_per_rule if value == best_percent)
        stage_count = len([s for s in (winning_rule.stages or []) if isinstance(s, dict)])
        return best_percent, winners, True, stage_count

    resolved = resolve_rule_type_to_best_value(rules, rule_type)
    if resolved is None:
        return None
    percent, ids = resolved
    return percent, ids, False, None


def resolve_discount_trigger(
    rules: list[NegotiationRule], context: GuestNegotiationContext, stage_index: int = 0
) -> ResolvedDiscount | None:
    """Resolves which (if any) of the three discount_* trigger types
    authorizes a discount for this guest, given only approved rules already
    fetched for this host. Pure function -- no DB access, no I/O.

    Precedence among the three trigger types themselves (repeat-guest vs.
    guest-requests) is the EXISTING behavior from pricing_engine.py, made
    explicit: repeat-guest eligibility, when the guest actually qualifies
    AND a discount_repeat_guest rule is approved, wins over
    discount_guest_requests -- this was already true before Phase 4 (see
    negotiate_rate's pre-existing if/elif chain); not a new precedence rule
    invented here. discount_no_ask is intentionally NOT resolved by this
    function -- it's read directly by callers that need "what to offer with
    no ask at all" (a different call shape than negotiate_rate's own
    guest-initiated-pushback path), matching how this rule_type is already
    unused by negotiate_rate today (confirmed: no code path reads
    discount_no_ask at all currently -- see the Phase 4 final report's
    Generalization Gaps for this specific, pre-existing, unrelated-to-this-
    phase gap).

    stage_index (Phase 4D, default 0): only relevant to a STAGED
    discount_repeat_guest or discount_guest_requests rule (see
    resolve_rule_type_to_best_staged_or_flat_value) -- ignored entirely by
    any flat rule, so every pre-Phase-4D call site that never passes it
    (stage_index=0 with no rule having `stages` set) resolves byte-
    identically to before this phase. Per the ratified Phase 4C decision
    (Decisions Log item 2): repeat-guest bypasses ANY discount_guest_requests
    ladder entirely when an approved discount_repeat_guest rule applies --
    this function already structurally guarantees that, since it returns
    the repeat-guest branch's result immediately and never falls through to
    the guest_requests branch below it when context.is_repeat_guest is True
    and a discount_repeat_guest rule resolves. discount_repeat_guest ITSELF
    may independently be staged or flat (its own stages column, entirely
    host-configured) -- that is a different question from whether it beats
    a guest_requests ladder, which this function's control flow already
    settles unconditionally."""
    if context.is_repeat_guest:
        resolved = resolve_rule_type_to_best_staged_or_flat_value(rules, "discount_repeat_guest", stage_index)
        if resolved is not None:
            percent, ids, is_staged, stage_count = resolved
            return ResolvedDiscount(
                percent=percent,
                source_rule_ids=ids,
                reason=REASON_REPEAT_GUEST,
                is_staged=is_staged,
                stage_index=stage_index if is_staged else None,
                stage_count=stage_count,
            )

    resolved = resolve_rule_type_to_best_staged_or_flat_value(rules, "discount_guest_requests", stage_index)
    if resolved is not None:
        percent, ids, is_staged, stage_count = resolved
        return ResolvedDiscount(
            percent=percent,
            source_rule_ids=ids,
            reason=REASON_GUEST_REQUESTS,
            is_staged=is_staged,
            stage_index=stage_index if is_staged else None,
            stage_count=stage_count,
        )

    return None


def resolve_custom_property_ceiling(
    property_rules: list[NegotiationRule], stage_index: int = 0
) -> ResolvedDiscount | None:
    """Resolves a property-scoped rule_type="custom" concession -- the one
    existing rule type that can RAISE the negotiation ceiling itself, not
    just supply a candidate discount value (see pricing_engine.py's own
    comment on why: "an approved 20% custom rule was silently re-clamped
    back down to the default 15% ceiling" was a confirmed-live bug this
    behavior already fixes, pre-Phase-4). `property_rules` must already be
    scoped to one property (see pricing_engine._approved_property_pricing_rules)
    -- this function does not itself filter by property_ids, matching the
    existing caller contract.

    stage_index (Phase 4D, default 0): a property-specific `custom` rule
    can itself be staged (Phase 4B's Host F validation case -- a
    property-specific STAGED policy -- requires stages to be a table-wide
    column, not restricted to the three discount_* trigger types; see
    NegotiationRule.stages' own docstring). Ignored by a flat custom rule,
    same backward-compatible default as resolve_discount_trigger above."""
    resolved = resolve_rule_type_to_best_staged_or_flat_value(property_rules, "custom", stage_index)
    if resolved is None:
        return None
    percent, ids, is_staged, stage_count = resolved
    return ResolvedDiscount(
        percent=percent,
        source_rule_ids=ids,
        reason=REASON_CUSTOM,
        is_staged=is_staged,
        stage_index=stage_index if is_staged else None,
        stage_count=stage_count,
    )
