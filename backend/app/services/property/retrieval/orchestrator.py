"""The actual recommend_properties pipeline: filter -> SQL search ->
(conditionally) semantic search -> merge/rank -> build context.
app/services/tool_handlers.py's handle_recommend_properties is a one-line
delegate to this module.

Semantic search only fires when args.purpose_of_stay is present (the one
genuinely subjective field in RecommendPropertiesArgs) AND the SQL layer
under-returned (fewer than 3 results) -- never for a purely structured
query, and never as a replacement for SQL filtering. See semantic_search.py
for the full reasoning and fail-open guarantees.
"""

import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services import calendar_service, pricing_engine
from app.services.pricing_engine import StayPrice
from app.services.amenity_taxonomy import canonicalize_amenities
from app.services.property.budget import UPSELL_MAX_OVER_BUDGET, BudgetConstraint
from app.services.property.pitch_formatter import RecommendationResult
from app.services.property.retrieval import context_builder, filter_builder, ranking, semantic_search, sql_search

logger = logging.getLogger(__name__)

_MIN_RESULTS_BEFORE_SEMANTIC_ENRICHMENT = 3
_MAX_OPTIONS_SHOWN = 3
# The combo path ("book two units together") shows up to 4 smaller units.
_MAX_COMBO_UNITS_SHOWN = 4
# Candidates priced for budget eligibility. Stage 1 narrows on non-price
# constraints only, so this bounds pricing work for very large portfolios
# (evaluate_stay_prices is 2 DB queries + cached-rate lookups, no live API
# calls, so pricing a few dozen candidates stays in the tens of ms).
_BUDGET_CANDIDATE_POOL = 50


async def recommend_properties(
    db: AsyncSession,
    args: RecommendPropertiesArgs,
    host_user_id: uuid.UUID,
    check_in: date | None = None,
    check_out: date | None = None,
    nights: int | None = None,
    call_session_id: uuid.UUID | None = None,
    amenity_weights: dict[str, float] | None = None,
    stay_check_in: date | None = None,
    stay_check_out: date | None = None,
    stay_window_start: date | None = None,
    budget_basis_assumed: bool = False,
) -> RecommendationResult:
    """check_in/check_out/nights are optional and NOT part of
    RecommendPropertiesArgs itself (the LLM-facing tool schema deliberately
    has no date fields -- recommend_properties's job is matching
    budget/guests/location/purpose, not availability). When the caller
    (app/voice/tools.py's wrapper) already knows the guest's dates from
    ConversationState.slots, threading them through here excludes/flags
    already-booked properties from the candidate set up front, instead of
    the guest being recommended a property and only finding out it's
    unavailable on a later check_calendar call (Phase 2.4, later superseded
    by Implementation 3's partial-availability classification below).

    amenity_weights is the same shape/sourcing as check_in/check_out -- not
    part of RecommendPropertiesArgs (it's derived from ConversationState's
    attention tracking, not a guest-facing tool argument), optional,
    threaded straight through to sql_search.run_sql_search's amenity boost.
    See filter_builder.apply_amenity_boost's own docstring for what it does.

    stay_check_in/stay_check_out (EXACT dates only, never a loose window) and
    stay_window_start (a loose window's start, used only as a cache probe)
    feed applicable-stay pricing; the stay length is args.nights. Unlike
    check_in/check_out above, which may be a loose window for the
    availability scan. budget_basis_assumed: the basis question was already
    asked once this call -- see budget.normalize_budget's assume_per_night.

    Budget eligibility (two stages): SQL narrows on non-price constraints
    only (filter_builder), then every candidate is priced with
    pricing_engine.evaluate_stay_prices (cached live rates, base_price, the
    host's automatic length-of-stay offer) and the budget is applied to that
    applicable price before ranking is capped for display."""
    budget = args.budget_constraint(assume_per_night=budget_basis_assumed)
    if budget is not None and budget.clarification is not None:
        # Explicit budget semantics: the stated budget can't be turned into a
        # ceiling yet (basis unknown for a multi-night/unknown-length stay,
        # a total with no stay length, or a non-INR amount). Searching anyway
        # would silently pick one reading -- exactly the per-night/total mixup
        # this exists to prevent -- so return a clarification result instead.
        _log_decision(args, budget, call_session_id, candidate_count=0)
        return RecommendationResult(options=[], budget=budget)
    budget_active = budget is not None and budget.is_applicable

    base_stmt = filter_builder.build_base_filters(args, host_user_id)
    sql_results, combo_note = await sql_search.run_sql_search(
        db, base_stmt, args, amenity_weights, candidate_pool=_BUDGET_CANDIDATE_POOL if budget_active else None
    )

    partially_available: list[tuple[Property, list[tuple[date, date]]]] = []
    if check_in is not None and check_out is not None and sql_results:
        # Fail open on any error -- an availability pre-check is a UX
        # improvement, never a reason a recommendation should be blocked
        # entirely if it errs/times out. check_calendar still catches a real
        # conflict downstream regardless.
        try:
            # Availability-first recommendations, Implementation 3: replaces
            # Implementation 2's hard unavailable_property_ids exclusion with
            # partial-availability classification -- "full" stays eligible
            # for recommendation exactly as before; "none" is excluded
            # exactly as the old hard exclusion already did; "partial" is
            # held OUT of the main recommended list (never presented as a
            # clean match) but retained separately so the agent can still
            # name the real conflicting dates instead of silently dropping
            # the property with no explanation. nights defaults to the
            # requested stay length implied by check_in/check_out itself
            # when not given explicitly (the common case: the guest gave
            # exact dates, not a separate night count).
            window_nights = nights if nights is not None else (check_out - check_in).days
            results_by_id = await calendar_service.partial_availability_for_candidates(
                db, [p.id for p in sql_results], check_in, check_out, window_nights
            )
            excluded_count = sum(1 for r in results_by_id.values() if r.status == "none")
            partial_count = sum(1 for r in results_by_id.values() if r.status == "partial")
            if excluded_count or partial_count:
                # Availability-first recommendations, Implementation 2: real
                # signal that the pre-filter is actually excluding/flagging a
                # candidate on a live call, not just passing in tests --
                # logger.info, not .debug, so it's visible without turning
                # on debug logging in production.
                logger.info(
                    "recommend_properties availability pre-filter: %d excluded (none), %d partial "
                    "(call_session_id=%s, check_in=%s, check_out=%s)",
                    excluded_count,
                    partial_count,
                    call_session_id,
                    check_in,
                    check_out,
                )
            partially_available = [
                (p, results_by_id[p.id].conflicting_bookings)
                for p in sql_results
                if results_by_id[p.id].status == "partial"
            ]
            sql_results = [p for p in sql_results if results_by_id[p.id].status == "full"]
        except Exception:
            partially_available = []

    semantic_results = []
    if args.purpose_of_stay and len(sql_results) < _MIN_RESULTS_BEFORE_SEMANTIC_ENRICHMENT:
        candidate_ids = [p.id for p in sql_results] if sql_results else []
        # Only enrich within the SQL candidate set when one exists (budget/
        # location/amenities already scoped it); with zero SQL results and
        # no scoping filters at all, fall back to searching this host's
        # full portfolio so a purely subjective query ("something romantic")
        # isn't guaranteed to return nothing.
        if not candidate_ids and not any(
            [args.budget_amount, args.preferred_location, args.required_amenities, args.num_guests]
        ):
            candidate_ids = list(
                (await db.scalars(select(Property.id).where(Property.user_id == host_user_id))).all()
            )
        semantic_results = await semantic_search.run_semantic_search(db, args.purpose_of_stay, candidate_ids)

    merged = ranking.merge_and_rank(sql_results, semantic_results)
    candidate_count = len(merged)

    # Applicable stay pricing: needed for budget eligibility, and whenever
    # the stay length is known so the host's automatic length-of-stay
    # offers can be pitched. No live API calls -- see evaluate_stay_prices.
    stay_prices: dict[uuid.UUID, StayPrice] = {}
    if merged and (budget_active or args.nights or (stay_check_in and stay_check_out)):
        stay_prices = await pricing_engine.evaluate_stay_prices(
            db,
            merged,
            host_user_id,
            check_in=stay_check_in,
            check_out=stay_check_out,
            nights=args.nights,
            window_start=stay_window_start,
        )

    # Only properties strictly within the guest's budget -- on the applicable
    # price, in the guest's own basis -- are recommended as matches. Those
    # within NEAR_BUDGET_STRETCH are kept apart as labelled near-budget
    # alternatives (never for a derived "something cheaper" ceiling the
    # guest never stated); anything further over, or with no usable price,
    # is dropped. Filtering preserves the ranked order.
    near_budget: list[Property] = []
    over_budget: list[Property] = []
    if budget_active:
        def _price(p: Property) -> tuple[float, float | None]:
            sp = stay_prices[p.id]
            return sp.per_night, sp.total

        if not args.cheaper_than_shown:
            near_budget = [p for p in merged if budget.within_stretch(*_price(p))]
        over_budget = [p for p in merged if not budget.fits(*_price(p))]
        merged = [p for p in merged if budget.fits(*_price(p))]

    # Recommendation conversations ("Phase X"): diversify_leading_candidates
    # rotates purely by price within a comparable-price band -- it has no
    # awareness of is_premium/amenity-match ranking. sql_search.py already
    # ran apply_premium_boost/apply_amenity_boost when more_premium_than_shown
    # or required_amenities was set, and that order carries real meaning (the
    # guest's own "more premium"/amenity request), not an arbitrary
    # price-ascending tie among otherwise-equivalent options -- rotating it
    # for variety would undo the exact boost the guest just asked for (e.g.
    # a premium pick silently rotated to 2nd place because a cheaper,
    # non-premium option happens to fall in the same price band). Same
    # "skip diversification, this order already means something" reasoning
    # as the combo_note branch below.
    boost_ordering_active = bool(args.more_premium_than_shown or args.required_amenities)
    if combo_note:
        # sql_search deliberately over-fetches to 4 on this path (no single
        # property sleeps the full group, so all 4 smaller units are worth
        # showing for combining) -- capping to 3 here would silently drop
        # one of the units the combo_note itself is telling the guest to
        # consider pairing. Only cap the normal (non-combo) path to 3,
        # matching sql_search's own primary-result limit. Diversity rotation
        # (below) deliberately does NOT apply here -- this list's order
        # already carries its own meaning (which units to pair up), not a
        # ranked "pick one" recommendation.
        properties_to_show = merged[:_MAX_COMBO_UNITS_SHOWN]
    elif boost_ordering_active:
        properties_to_show = merged[:_MAX_OPTIONS_SHOWN]
    else:
        # Phase 2.5 (documentation/agent-conversation-improvement.md):
        # rotate which property leads among a comparable-price band at the
        # front of the list, seeded off call_session_id, before capping to
        # 3 for display -- otherwise any two guests with similar-enough
        # criteria get the exact same top-of-portfolio property every time
        # (sql_search's own price-ascending order is deterministic and
        # identical across different calls). Only reorders among options
        # already within the comparable band; a clearly-best match is
        # completely unaffected.
        diversified = ranking.diversify_leading_candidates(merged, str(call_session_id) if call_session_id else None)
        properties_to_show = diversified[:_MAX_OPTIONS_SHOWN]

    # Near-budget alternatives only fill otherwise-empty slots -- they never
    # displace a real within-budget match.
    near_budget_to_show = near_budget[: max(0, _MAX_OPTIONS_SHOWN - len(properties_to_show))]
    upsell, upsell_reason = None, ""
    if budget_active and not args.cheaper_than_shown and not combo_note:
        shown_ids = {p.id for p in properties_to_show} | {p.id for p in near_budget_to_show}
        upsell, upsell_reason = _pick_upsell(
            [p for p in over_budget if p.id not in shown_ids], properties_to_show, args, budget, stay_prices
        )
    _log_decision(
        args,
        budget,
        call_session_id,
        candidate_count=candidate_count,
        pricing_evaluated_count=len(stay_prices),
        estimate_count=sum(1 for sp in stay_prices.values() if sp.is_estimate),
        eligible_count=len(merged) if budget_active else None,
        matched_count=len(properties_to_show),
        near_budget_count=len(near_budget_to_show),
        offer_count=sum(1 for p in properties_to_show if p.id in stay_prices and stay_prices[p.id].offer_percent),
        upsell_count=1 if upsell is not None else 0,
    )
    return context_builder.build_recommendation_result(
        properties_to_show,
        combo_note if properties_to_show else "",
        args,
        partially_available=partially_available,
        near_budget=near_budget_to_show,
        budget=budget,
        stay_prices=stay_prices,
        upsell=upsell,
        upsell_reason=upsell_reason,
    )


def _pick_upsell(
    candidates: list[Property],
    shown: list[Property],
    args: RecommendPropertiesArgs,
    budget: BudgetConstraint,
    stay_prices: dict[uuid.UUID, StayPrice],
) -> tuple[Property | None, str]:
    """At most one better-but-pricier property to pitch after the in-budget
    options ("I have another option, a bit steeper..."), per product
    direction: Mira sells, and a guest who hears a genuinely better option
    with its offer up front often takes it.

    "Better" is only ever a real, deterministic signal -- never price alone:
    a host-flagged premium property when nothing shown is premium, or one
    that has more of the guest's requested amenities than any shown option.
    It must be priced, and no more than UPSELL_MAX_OVER_BUDGET over the
    budget (on the applicable price, offer included). Among qualifying
    candidates: premium first, then most requested amenities, then the one
    closest to the guest's budget."""
    requested = set(canonicalize_amenities(args.required_amenities or []))

    def _matched(p: Property) -> set[str]:
        return requested & set(canonicalize_amenities(p.amenity_tags or []))

    best_shown_match_count = max((len(_matched(p)) for p in shown), default=0)
    shown_has_premium = any(p.is_premium for p in shown)
    best: tuple[tuple, Property, str] | None = None
    for p in candidates:
        sp = stay_prices.get(p.id)
        if sp is None or not budget.over_but_within(UPSELL_MAX_OVER_BUDGET, sp.per_night, sp.total):
            continue
        reasons = []
        if p.is_premium and not shown_has_premium:
            reasons.append("one of the host's premium properties")
        matched = _matched(p)
        if len(matched) > best_shown_match_count:
            what = " and ".join(sorted(a.replace("_", " ") for a in matched))
            reasons.append(f"it has the {what} they asked for")
        if not reasons:
            continue
        key = (p.is_premium, len(matched), -(sp.total if sp.total is not None else sp.per_night))
        if best is None or key > best[0]:
            best = (key, p, " -- ".join(reasons))
    return (best[1], best[2]) if best else (None, "")


def _log_decision(
    args: RecommendPropertiesArgs,
    budget: BudgetConstraint | None,
    call_session_id: uuid.UUID | None,
    *,
    candidate_count: int,
    pricing_evaluated_count: int = 0,
    estimate_count: int = 0,
    eligible_count: int | None = None,
    matched_count: int = 0,
    near_budget_count: int = 0,
    offer_count: int = 0,
    upsell_count: int = 0,
) -> None:
    """One line per recommendation decision, so a live call's "why did/
    didn't it offer X" is answerable from logs alone. Search criteria and
    counts only -- no guest name/phone."""
    logger.info(
        "recommend_properties_decision call_session_id=%s budget_amount=%s budget_basis=%s basis_assumed=%s "
        "budget_currency=%s nights=%s max_nightly_rate=%s max_total_rate=%s budget_clarification=%s location=%s "
        "num_guests=%s amenities=%s candidate_count=%d pricing_evaluated_count=%d estimate_count=%d "
        "eligible_count=%s matched_count=%d near_budget_count=%d offer_count=%d upsell_count=%d",
        call_session_id,
        budget.amount if budget else None,
        budget.basis if budget else None,
        budget.basis_assumed if budget else None,
        budget.currency if budget else None,
        args.nights,
        budget.max_nightly_rate if budget else None,
        budget.max_total_rate if budget else None,
        budget.clarification if budget else None,
        args.preferred_location,
        args.num_guests,
        args.required_amenities,
        candidate_count,
        pricing_evaluated_count,
        estimate_count,
        eligible_count,
        matched_count,
        near_budget_count,
        offer_count,
        upsell_count,
    )
