"""Tier 1 pricing (get_pricing) + a simple, rule-based Tier 3 negotiation
stub (negotiate_rate). The full PriceLabs/competitor-monitoring negotiation
engine described in the spec is Tier 3 (weeks 13-20) and intentionally not
built here — this gives the tool a real, useful response in the meantime.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import redis_client
from app.integrations.searchapi_client import (
    fetch_listing_total_price,
    fetch_property_coordinates,
    nightly_rate_cache_key,
)
from app.models.guest_profile import GuestProfile
from app.models.negotiation_rule import NegotiationRule
from app.models.pricing_rule import PricingRule
from app.models.property import Property
from app.models.user import User
from app.services import negotiation_policy

if TYPE_CHECKING:
    from app.voice.conversation_state import NegotiationEvent

logger = logging.getLogger(__name__)

MAX_NEGOTIATION_DISCOUNT_PERCENT = 15.0


@dataclass
class PriceBreakdown:
    nights: int
    base_total: float
    discount_percent: float
    discount_amount: float
    total: float
    per_night_avg: float
    # Phase 6 (Negotiation engine): flat rupee fees, quoted only when the
    # guest explicitly asked for early check-in/late checkout (see
    # tool_handlers.handle_get_pricing) -- never volunteered, and never
    # folded into base_total/discount_amount/total above, since those three
    # fields are read by existing callers (e.g. the /pricing/quote dashboard
    # endpoint, tests) that must keep seeing exactly today's stay-price math
    # regardless of whether a fee applies. 0.0 (not None) when no fee
    # applies/was requested, so callers can always do arithmetic on this
    # without a None-check -- same "additive field, harmless default"
    # discipline as every other PriceBreakdown field.
    early_checkin_fee: float = 0.0
    late_checkout_fee: float = 0.0


async def _approved_negotiation_rules(db: AsyncSession, host_id: uuid.UUID | None) -> list[NegotiationRule]:
    """Fail-closed, same discipline as _get_host_negotiation_policy below --
    any lookup failure (no host_id, DB error) returns an empty list rather
    than blocking/erroring calculate_price/negotiate_rate, which run live
    mid-call."""
    if host_id is None:
        return []
    try:
        return list(
            (
                await db.scalars(
                    select(NegotiationRule).where(
                        NegotiationRule.host_id == host_id,
                        NegotiationRule.status == "approved",
                    )
                )
            ).all()
        )
    except Exception:
        logger.exception("Negotiation rule lookup failed for host_id=%s -- ignoring", host_id)
        return []


async def _approved_property_pricing_rules(
    db: AsyncSession, host_id: uuid.UUID | None, property_id: uuid.UUID
) -> list[NegotiationRule]:
    """The stay-pricing subset of a host's approved negotiation rules that
    apply to this specific property. Filters property_ids in Python once
    fetched (not a JSONB containment WHERE clause) -- same reasoning
    filter_builder.matches_landmark already documents for its own
    small-per-host-candidate-set matching: no performance reason to push
    this into fragile JSONB-path SQL when the candidate set (one host's
    approved rules) is tiny. Unlike the host-wide discount_* trigger types
    (see _get_host_negotiation_policy), stay-pricing rule types always
    require an explicit, host-picked property match -- an empty
    property_ids here means "matches nothing yet," not "matches everything."
    """
    property_id_str = str(property_id)
    return [
        rule
        for rule in await _approved_negotiation_rules(db, host_id)
        if property_id_str in (rule.property_ids or [])
    ]


def _condition_number(condition: dict, key: str) -> float | None:
    """Safely reads a numeric value out of a NegotiationRule/PricingRule's
    free-form JSONB condition -- condition is host-editable (directly via
    PATCH, not just LLM-parsed), so a malformed value (e.g. condition={"fee":
    "free"} from a bad client/edit) must never reach a bare float()/comparison
    call on the live calculate_price/negotiate_rate/check_calendar path.
    Returns None (treated as "this rule doesn't apply") on anything
    non-numeric, same fail-closed discipline _approved_property_pricing_rules
    already applies to the DB lookup itself, extended to cover the values
    once fetched."""
    value = condition.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def _length_of_stay_discount_percent(
    db: AsyncSession, property_id: uuid.UUID, nights: int, host_id: uuid.UUID | None = None
) -> float:
    rules = (
        await db.scalars(
            select(PricingRule).where(
                PricingRule.property_id == property_id,
                PricingRule.rule_type == "length_of_stay",
                PricingRule.active.is_(True),
            )
        )
    ).all()

    best = 0.0
    for rule in rules:
        min_nights = rule.condition.get("min_nights") if isinstance(rule.condition, dict) else None
        if min_nights is not None and nights >= min_nights:
            best = max(best, float(rule.discount_percent))

    # The host-authored, multi-property NegotiationRule is a SECOND source
    # for this same rule_type (see that model's docstring for why it's a
    # separate table from PricingRule, not a replacement) -- read alongside
    # the existing per-property PricingRule rows above, same "take the
    # best/max applicable discount" resolution, never double-counted or
    # preferred over the other by ordering.
    for rule in await _approved_property_pricing_rules(db, host_id, property_id):
        if rule.rule_type != "length_of_stay" or rule.discount_percent is None or not isinstance(rule.condition, dict):
            continue
        min_nights = _condition_number(rule.condition, "min_nights")
        if min_nights is not None and nights >= min_nights:
            best = max(best, float(rule.discount_percent))
    return best


def _stay_includes_weekend_night(check_in: date, check_out: date) -> bool:
    """A stay "includes a weekend" if any night actually slept (check_in
    inclusive, check_out exclusive -- the same [check_in, check_out) range
    every other date-range check in this codebase uses) is a Friday or
    Saturday. Friday=4, Saturday=5 (date.weekday()'s Monday=0 convention)."""
    nights = (check_out - check_in).days
    return any((check_in + timedelta(days=i)).weekday() in (4, 5) for i in range(nights))


async def minimum_stay_nights_violation(
    db: AsyncSession, host_id: uuid.UUID | None, property_id: uuid.UUID, check_in: date, check_out: date
) -> int | None:
    """Returns the strictest applicable minimum-nights requirement if the
    requested stay is below it, else None. Read by tool_handlers.
    handle_check_calendar ALONGSIDE its existing flat Property.minimum_nights
    check (this never replaces that check -- it only adds a second,
    conditional floor a host can configure via the AI Training/Pricing page,
    same "additive, host-opt-in, zero rules configured = zero behavior
    change" guarantee every other rule type in this module follows).
    weekend_min_nights only applies when the stay actually includes a Friday
    or Saturday night -- a host with no such rule, or a guest whose stay is
    purely weekday, sees no change at all."""
    nights = (check_out - check_in).days
    strictest: float | None = None
    for rule in await _approved_property_pricing_rules(db, host_id, property_id):
        if rule.rule_type != "minimum_stay_nights" or not isinstance(rule.condition, dict):
            continue
        general_min = _condition_number(rule.condition, "min_nights")
        if general_min is not None and nights < general_min:
            strictest = general_min if strictest is None else max(strictest, general_min)
        weekend_min = _condition_number(rule.condition, "weekend_min_nights")
        if weekend_min is not None and nights < weekend_min and _stay_includes_weekend_night(check_in, check_out):
            strictest = weekend_min if strictest is None else max(strictest, weekend_min)
    return int(strictest) if strictest is not None else None


async def _flat_fee(
    db: AsyncSession, host_id: uuid.UUID | None, property_id: uuid.UUID, rule_type: str
) -> float:
    """early_checkin_fee/late_checkout_fee -- flat rupee amounts, condition
    shape {"fee": N}. Takes the max if a host somehow has more than one
    approved rule of the same type for a property (shouldn't normally
    happen via the UI, but never silently pick an arbitrary one)."""
    best = 0.0
    for rule in await _approved_property_pricing_rules(db, host_id, property_id):
        if rule.rule_type != rule_type or not isinstance(rule.condition, dict):
            continue
        fee = _condition_number(rule.condition, "fee")
        if fee is not None:
            best = max(best, fee)
    return best


async def _sum_cached_nightly_rates(listing_id: str, check_in: date, check_out: date) -> float | None:
    """Tries the daily cache-warm job's rolling near-term window first (see
    smart_pricing_service.refresh_live_pricing_cache) -- if every night in
    [check_in, check_out) has a cached rate, sums them for an instant
    answer with zero live API calls and none of the latency a mid-call
    fetch adds. Returns None the moment any single night is missing from
    cache (a request outside the cached window, or a night that failed to
    resolve when the job ran), so calculate_price knows to fall through to
    a live fetch for the full range rather than quoting a partial total."""
    nights = (check_out - check_in).days
    total = 0.0
    for i in range(nights):
        night = check_in + timedelta(days=i)
        cached = await redis_client.cache_get_json(nightly_rate_cache_key(listing_id, night))
        if cached is None:
            return None
        total += cached
    return round(total, 2)


async def calculate_price(
    db: AsyncSession,
    property_: Property,
    check_in: date,
    check_out: date,
    apply_discounts: bool = True,
    host_id: uuid.UUID | None = None,
    requested_early_checkin: bool = False,
    requested_late_checkout: bool = False,
) -> PriceBreakdown:
    """host_id/requested_early_checkin/requested_late_checkout (Phase 6,
    Negotiation engine) are all optional and default to no-ops -- every
    existing call site (the /pricing/quote dashboard endpoint, negotiate_rate
    below) keeps its exact current behavior unless it explicitly opts in.
    host_id is required for length_of_stay/early_checkin_fee/late_checkout_fee
    to read NegotiationRule at all (see _approved_property_pricing_rules) --
    omitting it simply means "no host-authored rules apply," never an
    error, matching this module's existing fail-closed discipline."""
    nights = (check_out - check_in).days
    base_price = float(property_.base_price)

    live_total: float | None = None
    if property_.exact_airbnb_pricing and property_.airbnb_listing_id:
        # Airbnb Smart Pricing changes the listing's rate daily/per-date --
        # no static base_price can stay accurate for a host on it (confirmed
        # live: Property.base_price was already stale again days after being
        # manually corrected). Fetch this exact listing's current price for
        # these exact dates instead of trusting the stored number.
        #
        # Try the daily cache-warm job's near-term window first (see
        # smart_pricing_service.refresh_live_pricing_cache) -- if it fully
        # covers these dates, this is instant with zero live API calls, no
        # mid-call latency. Only falls through to a live fetch (below) for
        # dates outside that cached window.
        live_total = await _sum_cached_nightly_rates(property_.airbnb_listing_id, check_in, check_out)

        if live_total is None:
            # Coordinates are cached permanently once resolved (a listing's
            # location doesn't change) -- otherwise this is a single live
            # API call per pricing question, scoped to a tight bounding_box
            # around the listing's own coordinates. A plain city-wide search
            # does NOT reliably include this specific listing (confirmed
            # live: a real 20-listing city search for a real listing never
            # included it) -- see searchapi_client.fetch_listing_total_price.
            #
            # Falls back to the static base_price/night math below on any
            # failure (coordinates unresolvable, listing not bookable for
            # these exact dates, API down) -- never blocks a live pricing
            # quote on this call succeeding.
            if property_.airbnb_latitude is None or property_.airbnb_longitude is None:
                coords = await fetch_property_coordinates(property_.airbnb_listing_id)
                if coords is not None:
                    property_.airbnb_latitude, property_.airbnb_longitude = coords
                    await db.commit()

            if property_.airbnb_latitude is not None and property_.airbnb_longitude is not None:
                live_total = await fetch_listing_total_price(
                    float(property_.airbnb_latitude),
                    float(property_.airbnb_longitude),
                    property_.airbnb_listing_id,
                    check_in,
                    check_out,
                )

    base_total = round(live_total, 2) if live_total is not None else round(base_price * nights, 2)

    discount_percent = 0.0
    if apply_discounts:
        discount_percent = await _length_of_stay_discount_percent(db, property_.id, nights, host_id=host_id)
    discount_amount = round(base_total * discount_percent / 100, 2)

    total = round(base_total - discount_amount, 2)

    # Fees are looked up (fail-closed, defaults to 0.0 on any failure/no
    # rule) only when the guest actually asked -- tool_handlers.py never
    # sets requested_early_checkin/requested_late_checkout True unless the
    # guest explicitly requested it this call, so a host with such a rule
    # configured never has it volunteered unprompted (see GOLDEN_RULES).
    early_checkin_fee = 0.0
    if requested_early_checkin:
        early_checkin_fee = await _flat_fee(db, host_id, property_.id, "early_checkin_fee")
    late_checkout_fee = 0.0
    if requested_late_checkout:
        late_checkout_fee = await _flat_fee(db, host_id, property_.id, "late_checkout_fee")

    return PriceBreakdown(
        nights=nights,
        base_total=base_total,
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        total=total,
        early_checkin_fee=early_checkin_fee,
        late_checkout_fee=late_checkout_fee,
        per_night_avg=round(total / nights, 2) if nights else base_price,
    )


@dataclass
class NegotiationResult:
    accepted: bool
    counter_offer: float
    asking_price: float
    message: str
    refused: bool = False
    # Phase 4D -- structured Decision fields (Phase 4C Section M/Step 9),
    # additive to the pre-existing fields above, which keep their exact
    # pre-Phase-4D meaning for every caller that doesn't read the new ones.
    # None/False are the correct values whenever no staged policy applies
    # (every call before this phase, and every flat-only host after it),
    # so nothing about existing behavior changes -- these only carry real
    # information once a host has an approved rule with `stages` set.
    is_staged: bool = False
    stage_index: int | None = None
    stage_count: int | None = None
    progressed_this_event: bool = False
    exhausted: bool = False
    # Phase 4F self-review finding: the true policy floor for THIS turn,
    # independent of counter_offer -- counter_offer is guest_offer itself
    # (unclamped) on the accepted branch below, which can be strictly
    # ABOVE floor_price whenever the guest offered more than the floor
    # required. Without this, a caller has no way to tell "exhausted AND
    # counter_offer is genuinely the max" apart from "exhausted AND the
    # guest just happened to offer/accept a number above the max" -- see
    # state_prompt_sync._negotiation_hint, which used to phrase the
    # accepted branch as "this is the maximum you're authorized to offer"
    # even when floor_price was materially lower, wrongly telling the LLM
    # to refuse further guest pushback it was actually still authorized to
    # grant. Always populated (never None) -- floor_price is computed
    # unconditionally above, on every path through this function.
    floor_price: float = 0.0


@dataclass
class HostNegotiationPolicy:
    """Resolved, ready-to-use negotiation ALLOWANCE/CEILING for one host --
    either derived from the host's own User row, or the untouched global
    defaults if it has no override. Never constructed with a status other
    than "approved" rows -- see _get_host_negotiation_policy.

    Phase 4: no longer carries guest_requests_percent/repeat_guest_percent
    -- those are resolved directly in negotiate_rate now, via
    negotiation_policy.resolve_discount_trigger against the raw approved
    rule list (that function needs rule-level data such as
    source_rule_ids, which this already-flattened dataclass has no reason
    to carry). This dataclass's remaining job is purely the two host-level
    settings (negotiation_allowed, max_discount_percent) that come from
    User itself, not from any NegotiationRule row."""

    negotiation_allowed: bool
    max_discount_percent: float


async def _get_host_negotiation_policy(db: AsyncSession, host_id: uuid.UUID | None) -> HostNegotiationPolicy:
    """Derive-on-read from the host's User row (negotiation_allowed,
    max_discount_percent_override) -- never materialized per property, so
    an edit applies everywhere immediately.

    Mandatory fallback: any failure here (no host_id, DB error) returns
    today's exact pre-existing global-constant behavior. negotiate_rate
    must never error, hang, or silently default to a 0%/100% discount
    because of this lookup -- same "don't crash, don't block" discipline as
    BRIGHT_DATA_API_KEY/SMTP_* elsewhere in this codebase, since this runs
    live, mid-call, in the guest's negotiation path.
    """
    default_policy = HostNegotiationPolicy(
        negotiation_allowed=True,
        max_discount_percent=MAX_NEGOTIATION_DISCOUNT_PERCENT,
    )
    if host_id is None:
        return default_policy

    try:
        host = await db.get(User, host_id)
    except Exception:
        logger.exception("Host negotiation policy lookup failed for host_id=%s -- using global defaults", host_id)
        return default_policy

    # host.negotiation_allowed is only ever None for an in-memory User that
    # was never flushed through the DB (server_default populates real rows)
    # -- treat that the same as "unset", i.e. allowed, not as "disabled".
    negotiation_allowed = True if host is None or host.negotiation_allowed is None else host.negotiation_allowed
    max_discount_percent = (
        float(host.max_discount_percent_override)
        if host is not None and host.max_discount_percent_override is not None
        else MAX_NEGOTIATION_DISCOUNT_PERCENT
    )

    return HostNegotiationPolicy(
        negotiation_allowed=negotiation_allowed,
        max_discount_percent=max_discount_percent,
    )


async def _is_repeat_guest_for_host(db: AsyncSession, guest_profile_id: uuid.UUID | None) -> bool | None:
    """Real Guest Memory check (memory-architecture-plan.md section 1):
    GuestProfile is now host-scoped (phone, host_id), so total_stays >= 2
    genuinely means this guest has completed at least one prior call with
    THIS host, not just "the guest claims to be a repeat visitor." Returns
    None (not False) when no signal is available at all, so the caller can
    fall back to the LLM-supplied guest_loyalty instead of assuming "new."
    """
    if guest_profile_id is None:
        return None
    try:
        guest = await db.get(GuestProfile, guest_profile_id)
    except Exception:
        logger.exception("Guest memory lookup failed for guest_profile_id=%s -- falling back to guest_loyalty", guest_profile_id)
        return None
    if guest is None:
        return None
    return (guest.total_stays or 0) >= 2


async def negotiate_rate(
    db: AsyncSession,
    property_: Property,
    check_in: date,
    check_out: date,
    guest_offer: float | None,
    guest_loyalty: str = "new",
    host_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
    prior_events: list["NegotiationEvent"] | None = None,
) -> NegotiationResult:
    """prior_events (Phase 4D, generalized negotiation state -- see
    documentation design docs "Phase 4C: Negotiation Semantics Contract"):
    this call's negotiation history WITHIN THE CURRENT CALL, not including
    the guest_offer being resolved right now -- app/voice/tools.py's
    negotiate_rate wrapper passes state.negotiation_events (already
    property-scoped by ConversationState.record_negotiation_event) BEFORE
    appending this call's own event, so resolve_stage_index below only
    ever sees PRIOR events when deciding whether this NEW offer progresses
    a stage. Optional/None-default (-> stage 0, i.e. today's exact
    pre-Phase-4D behavior) so every pre-existing call site -- including
    every test in tests/test_pricing_engine.py that predates this phase --
    continues to resolve identically without passing it."""
    breakdown = await calculate_price(db, property_, check_in, check_out, apply_discounts=False)
    asking_price = breakdown.total

    policy = await _get_host_negotiation_policy(db, host_id)

    if not policy.negotiation_allowed:
        return NegotiationResult(
            accepted=False,
            counter_offer=asking_price,
            asking_price=asking_price,
            floor_price=asking_price,
            refused=True,
            message=(
                f"I checked, but ₹{asking_price:,.0f} for {breakdown.nights} nights is already our best price "
                f"on {property_.name} -- I'm not able to offer a further discount. Happy to connect you with "
                f"the host if you'd like to discuss it further."
            ),
        )

    # trigger_type="repeat_guest_same_host": prefer the real Guest Memory
    # signal (GuestProfile.total_stays, host-scoped) when a guest profile is
    # resolvable; fall back to the LLM-supplied guest_loyalty argument
    # ("returning"/"frequent") only when it isn't -- e.g. a caller_number
    # that never resolved to a profile. Never silently treat "no signal" as
    # "not a repeat guest" if the LLM itself said otherwise.
    guest_memory_signal = await _is_repeat_guest_for_host(db, guest_profile_id)
    if guest_memory_signal is not None:
        is_repeat_guest = guest_memory_signal
    else:
        is_repeat_guest = guest_loyalty in ("returning", "frequent")

    negotiation_rules = await _approved_negotiation_rules(db, host_id)
    property_rules = await _approved_property_pricing_rules(db, host_id, property_.id)

    # Phase 4D: the stage THIS TURN'S offer should be evaluated/authorized
    # against is derived from prior events PLUS this call's own guest_offer
    # -- resolve_stage_index's own progression rule (Phase 4C Section D)
    # already guarantees a first numeric offer lands on stage 0 regardless
    # of its size (there is no "highest_seen" yet to compare against), and
    # that a repeated/lower/None offer never advances past whatever prior
    # events already reached -- so including the current event in the
    # derivation is what makes THIS turn's resolved discount reflect
    # whether THIS offer just progressed, not the turn before it. The
    # stage COUNT that bounds this derivation must come from whichever
    # rule would actually be resolved -- but which rule resolves can
    # itself depend on is_repeat_guest, which doesn't depend on
    # stage_index at all, so there's no circularity: compute the candidate
    # stage counts up front from the raw approved rules (cheap, pure,
    # already-fetched data), independent of stage_index itself.
    candidate_stage_counts = [
        len(rule.stages) for rule in negotiation_rules + property_rules if rule.stages
    ]
    stage_count_for_derivation = max(candidate_stage_counts) if candidate_stage_counts else 0
    events = prior_events or []
    stage_index_before = negotiation_policy.resolve_stage_index(events, stage_count_for_derivation)
    if guest_offer is not None:
        # Deferred import -- see the identical note further below on why
        # this stays a cheap, non-circular import rather than a
        # module-level dependency on app/voice/*.
        from app.voice.conversation_state import NegotiationEvent

        current_event = NegotiationEvent(guest_offer=guest_offer, property_id=str(property_.id))
        stage_index = negotiation_policy.resolve_stage_index(events + [current_event], stage_count_for_derivation)
    else:
        # guest_offer=None never progresses (Phase 4C Section E/S.1.6) --
        # resolves against whatever stage prior events already reached.
        stage_index = stage_index_before

    # Phase 4: the repeat-guest-vs-guest-requests precedence itself is now
    # the SAME live function negotiation_policy.py's own tests exercise
    # (resolve_discount_trigger) -- previously this if/elif chain and that
    # function were two separate implementations of one precedence rule,
    # only the chain here was actually wired in. Fetches the raw approved
    # rules directly via _approved_negotiation_rules (the same helper
    # _approved_property_pricing_rules below also calls, just for a
    # different, property-filtered purpose) since resolve_discount_trigger
    # needs rule-level data (source_rule_ids), not the already-flattened
    # HostNegotiationPolicy floats _get_host_negotiation_policy returns.
    trigger_decision = negotiation_policy.resolve_discount_trigger(
        negotiation_rules,
        negotiation_policy.GuestNegotiationContext(is_repeat_guest=is_repeat_guest),
        stage_index=stage_index,
    )
    if trigger_decision is not None:
        discount_percent = trigger_decision.percent
        discount_percent_source = trigger_decision
    else:
        loyalty_bonus_percent = {"new": 0.0, "returning": 5.0, "frequent": 10.0}.get(guest_loyalty, 0.0)
        discount_percent = loyalty_bonus_percent + 10.0
        discount_percent_source = None

    # A rule_type="custom" NegotiationRule is a host-authored, per-property
    # freeform concession (e.g. "for this villa specifically, I can go to
    # 20% for a returning guest") -- takes priority over the chain above ONLY
    # if it's MORE generous, never less; a property-specific concession the
    # host explicitly approved should never leave the guest worse off than
    # the host-wide discount_* policy would already give them. Also
    # raises the CEILING (policy.max_discount_percent) itself, not just the
    # resolved discount_percent -- self-review fix: without this, an
    # approved 20% custom rule was silently re-clamped back down to the
    # default 15% ceiling two lines below, making the concession a no-op
    # for any value above MAX_NEGOTIATION_DISCOUNT_PERCENT/the host's own
    # override, exactly the case this rule type exists for.
    custom_ceiling = policy.max_discount_percent
    custom = negotiation_policy.resolve_custom_property_ceiling(property_rules, stage_index=stage_index)
    if custom is not None:
        # Phase 4D correctness fix (caught in self-review, not by the
        # original test suite): track which decision object actually
        # produced the winning discount_percent AT THE MOMENT this max()
        # is evaluated, not by re-comparing floats against the final
        # max_discount_percent afterward. Re-deriving "who won" from a
        # later float comparison breaks the moment custom.percent
        # coincidentally ties max_discount_percent for a reason OTHER than
        # actually being the winning candidate (e.g. custom_ceiling's own
        # max() below happens to land on the same number) -- confirmed via
        # a direct probe: a staged trigger_decision at 12% plus a flat
        # custom rule ALSO at 12% silently reported is_staged=False,
        # because custom.percent(12) == max_discount_percent(12) even
        # though custom never actually raised discount_percent above
        # trigger_decision's own 12%. This branch is evaluated inline with
        # the max() itself, so it can never disagree with which operand
        # actually won.
        if custom.percent > discount_percent:
            discount_percent_source = custom
        discount_percent = max(discount_percent, custom.percent)
        custom_ceiling = max(custom_ceiling, custom.percent)

    max_discount_percent = min(custom_ceiling, discount_percent)
    floor_price = round(asking_price * (1 - max_discount_percent / 100), 2)

    # Phase 4D Decision-contract fields: winning_decision is whichever
    # ResolvedDiscount actually produced discount_percent BEFORE the
    # ceiling clamp (tracked above, inline, never re-derived from a float
    # comparison after the fact). If the ceiling clamp (min() with
    # custom_ceiling) is what reduced the final number, that's a separate
    # concern from WHICH rule authorized the underlying percent -- this
    # field answers "which policy is the guest negotiating under," not
    # "which number happened to match the final price."
    winning_decision = discount_percent_source
    is_staged = winning_decision.is_staged if winning_decision is not None else False
    stage_count = winning_decision.stage_count if winning_decision is not None else None
    # progressed_this_event: did resolving THIS guest_offer move the stage
    # index forward from where prior events alone had already reached?
    # guest_offer=None can never progress (Phase 4C Section E/S.1.6) --
    # already guaranteed above, since stage_index falls back to
    # stage_index_before whenever guest_offer is None, making this
    # comparison False by construction in that case, not a separate check.
    progressed_this_event = is_staged and stage_index > stage_index_before
    exhausted = is_staged and stage_count is not None and stage_index >= stage_count - 1

    if guest_offer is None:
        # Guest asked us to name a price rather than stating their own offer --
        # propose our best price directly instead of comparing against a number.
        return NegotiationResult(
            accepted=True,
            counter_offer=floor_price,
            asking_price=asking_price,
            floor_price=floor_price,
            is_staged=is_staged,
            stage_index=stage_index if is_staged else None,
            stage_count=stage_count,
            progressed_this_event=False,
            exhausted=exhausted,
            message=(
                f"Good news -- for {property_.name} over {breakdown.nights} nights, I can bring it down to "
                f"₹{floor_price:,.0f} (our standard rate is ₹{asking_price:,.0f})."
            ),
        )

    if guest_offer >= floor_price:
        return NegotiationResult(
            accepted=True,
            counter_offer=guest_offer,
            asking_price=asking_price,
            floor_price=floor_price,
            is_staged=is_staged,
            stage_index=stage_index if is_staged else None,
            stage_count=stage_count,
            progressed_this_event=progressed_this_event,
            exhausted=exhausted,
            message=(
                f"Good news -- I was able to accept ₹{guest_offer:,.0f} for {property_.name} over "
                f"{breakdown.nights} nights."
            ),
        )

    return NegotiationResult(
        accepted=False,
        counter_offer=floor_price,
        asking_price=asking_price,
        floor_price=floor_price,
        is_staged=is_staged,
        stage_index=stage_index if is_staged else None,
        stage_count=stage_count,
        progressed_this_event=progressed_this_event,
        exhausted=exhausted,
        message=(
            f"I checked, but ₹{guest_offer:,.0f} is a bit below what I can do on {property_.name}. "
            f"The best I can offer for {breakdown.nights} nights is ₹{floor_price:,.0f} "
            f"(our standard rate is ₹{asking_price:,.0f})."
        ),
    )
