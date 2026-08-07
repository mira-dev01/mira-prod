"""Runs the deterministic SQL query built by filter_builder.py, including
today's "widen to smaller units if nobody sleeps the full group" fallback.
Moved verbatim out of handle_recommend_properties (app/services/
tool_handlers.py) as part of splitting that function apart.
"""

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.retrieval.filter_builder import (
    apply_amenity_boost,
    apply_guest_count_filter,
    apply_landmark_boost,
    apply_premium_boost,
)

# When required_amenities is set, fetch a wider candidate set than the final
# 3 shown -- required_amenities is a soft ranking preference now (see
# filter_builder.apply_amenity_boost), not a hard WHERE filter, so the SQL
# query itself can no longer guarantee the 3 cheapest rows are the ones that
# actually match. A wider pool gives the boost real candidates with the
# requested amenities to promote ahead of cheaper-but-non-matching ones,
# instead of only ever re-sorting within whichever 3 happened to be
# cheapest regardless of amenities.
_AMENITY_CANDIDATE_POOL_SIZE = 10


async def run_sql_search(
    db: AsyncSession, base_stmt: Select, args: RecommendPropertiesArgs
) -> tuple[list[Property], str]:
    """Returns (properties, combo_note) -- combo_note is a non-empty string
    only when the small-units fallback fired (no single property sleeps the
    full requested group, so smaller units are suggested for combining)."""
    stmt = apply_guest_count_filter(base_stmt, args)
    pool_size = _AMENITY_CANDIDATE_POOL_SIZE if args.required_amenities else 3
    stmt = stmt.order_by(Property.base_price.asc()).limit(pool_size)
    properties = list((await db.scalars(stmt)).all())

    combo_note = ""
    if not properties and args.num_guests is not None:
        # No single property sleeps the whole group -- fall back to smaller
        # units (same location/budget filters, just without the guest-count
        # cutoff) instead of a flat "nothing found", so the model can suggest
        # booking two units together to cover the group. Hosts with several
        # small 1BHKs at the same property (e.g. the Pause Project in Siolim)
        # routinely accommodate larger groups exactly this way.
        fallback_stmt = base_stmt.order_by(Property.base_price.asc()).limit(4)
        properties = list((await db.scalars(fallback_stmt)).all())
        if properties:
            combo_note = (
                f" None of these sleep all {args.num_guests} guests alone -- since they're separate units, "
                "suggest the guest book two of them together to cover the group."
            )

    properties = apply_landmark_boost(properties, args.near_landmark)
    properties = apply_premium_boost(properties, args.more_premium_than_shown)
    # Amenity boost runs LAST and re-caps to 3 -- it needs to work over the
    # WIDER pool_size candidate set fetched above, then trim back down to
    # the normal 3-result shape every other path already produces. Never
    # applied on the combo-fallback path (args.num_guests set AND combo_note
    # fired) -- that path's own 4-result list already carries its own
    # meaning ("combine these"), same reasoning ranking.diversify_leading_candidates
    # already uses to skip that path.
    if args.required_amenities and not combo_note:
        properties = apply_amenity_boost(properties, args.required_amenities)[:3]

    return properties, combo_note
