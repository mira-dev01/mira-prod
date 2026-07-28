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

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.pitch_formatter import RecommendationResult
from app.services.property.retrieval import context_builder, filter_builder, ranking, semantic_search, sql_search

_MIN_RESULTS_BEFORE_SEMANTIC_ENRICHMENT = 3


async def recommend_properties(
    db: AsyncSession, args: RecommendPropertiesArgs, host_user_id: uuid.UUID
) -> RecommendationResult:
    base_stmt = filter_builder.build_base_filters(args, host_user_id)
    sql_results, combo_note = await sql_search.run_sql_search(db, base_stmt, args)

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

    if combo_note:
        # sql_search deliberately over-fetches to 4 on this path (no single
        # property sleeps the full group, so all 4 smaller units are worth
        # showing for combining) -- capping to 3 here would silently drop
        # one of the units the combo_note itself is telling the guest to
        # consider pairing. Only cap the normal (non-combo) path to 3,
        # matching sql_search's own primary-result limit.
        properties_to_show = merged
    else:
        properties_to_show = merged[:3]

    return context_builder.build_recommendation_result(properties_to_show, combo_note)
