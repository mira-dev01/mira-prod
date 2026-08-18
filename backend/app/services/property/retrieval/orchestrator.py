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
from app.services import calendar_service
from app.services.property.pitch_formatter import RecommendationResult
from app.services.property.retrieval import context_builder, filter_builder, ranking, semantic_search, sql_search

logger = logging.getLogger(__name__)

_MIN_RESULTS_BEFORE_SEMANTIC_ENRICHMENT = 3


async def recommend_properties(
    db: AsyncSession,
    args: RecommendPropertiesArgs,
    host_user_id: uuid.UUID,
    check_in: date | None = None,
    check_out: date | None = None,
    nights: int | None = None,
    call_session_id: uuid.UUID | None = None,
    amenity_weights: dict[str, float] | None = None,
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
    See filter_builder.apply_amenity_boost's own docstring for what it does."""
    base_stmt = filter_builder.build_base_filters(args, host_user_id)
    sql_results, combo_note = await sql_search.run_sql_search(db, base_stmt, args, amenity_weights)

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
            [args.budget, args.preferred_location, args.required_amenities, args.num_guests]
        ):
            candidate_ids = list(
                (await db.scalars(select(Property.id).where(Property.user_id == host_user_id))).all()
            )
        semantic_results = await semantic_search.run_semantic_search(db, args.purpose_of_stay, candidate_ids)

    merged = ranking.merge_and_rank(sql_results, semantic_results)

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
        properties_to_show = merged
    elif boost_ordering_active:
        properties_to_show = merged[:3]
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
        properties_to_show = diversified[:3]

    return context_builder.build_recommendation_result(
        properties_to_show, combo_note, args, partially_available=partially_available
    )
