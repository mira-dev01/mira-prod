"""Runs the deterministic SQL query built by filter_builder.py, including
today's "widen to smaller units if nobody sleeps the full group" fallback.
Moved verbatim out of handle_recommend_properties (app/services/
tool_handlers.py) as part of splitting that function apart.
"""

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.retrieval.filter_builder import apply_guest_count_filter, apply_landmark_boost


async def run_sql_search(
    db: AsyncSession, base_stmt: Select, args: RecommendPropertiesArgs
) -> tuple[list[Property], str]:
    """Returns (properties, combo_note) -- combo_note is a non-empty string
    only when the small-units fallback fired (no single property sleeps the
    full requested group, so smaller units are suggested for combining)."""
    stmt = apply_guest_count_filter(base_stmt, args)
    stmt = stmt.order_by(Property.base_price.asc()).limit(3)
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

    return properties, combo_note
