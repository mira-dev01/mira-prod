"""Merges SQL and (optional) semantic search candidates into one ordered
list. Deliberately a simple weighted union, not a learned ranker -- SQL
order (price ascending, today's behavior) is always primary/authoritative;
a semantic-only match is appended after, never inserted ahead of a hard
SQL match. Future extension point: swap this module's internals for a
cross-encoder/LLM reranker without touching any other module in this
package -- the list[Property] -> list[Property] signature is stable
regardless of internal sophistication.
"""

import hashlib

from app.models.property import Property


def merge_and_rank(sql_results: list[Property], semantic_results: list[Property]) -> list[Property]:
    """sql_results keeps its own order (SQL is authoritative). Any
    semantic_results entry not already present is appended after, in the
    order semantic_search returned it -- this should rarely add anything in
    practice, since semantic_search only ever searches within the SQL
    candidate set (see that module's docstring), but the defensive path is
    kept simple rather than assumed impossible."""
    if not semantic_results:
        return list(sql_results)

    seen_ids = {p.id for p in sql_results}
    merged = list(sql_results)
    for property_ in semantic_results:
        if property_.id not in seen_ids:
            merged.append(property_)
            seen_ids.add(property_.id)
    return merged


# Phase 2.5 (documentation/agent-conversation-improvement.md): properties
# whose price differs by less than this fraction of the cheaper one are
# treated as "comparable" -- close enough that always showing the same one
# first (today's byte-for-byte price-ascending behavior) starts to look like
# an arbitrary tie-break rather than a real quality signal. Deliberately
# conservative (10%) -- this only ever reorders within a tight band, never
# trades a genuinely better/cheaper match for a worse one just for variety.
_COMPARABLE_PRICE_BAND = 0.10


def _rotation_seed(call_session_id: str | None) -> int:
    """Deterministic per-call seed -- the SAME call always gets the SAME
    rotation (a guest who hears 'Ocean View' first doesn't get 'Palm
    Retreat' first if they ask again in the same call), while DIFFERENT
    calls see genuine variety. Falls back to 0 (today's byte-identical
    ordering, no rotation) when no call_session_id is available (e.g. a
    direct/test call site that doesn't have one) -- fails open to the
    existing deterministic behavior rather than guessing."""
    if not call_session_id:
        return 0
    digest = hashlib.sha256(call_session_id.encode()).digest()
    return int.from_bytes(digest[:4], "big")


def diversify_leading_candidates(
    properties: list[Property], call_session_id: str | None = None
) -> list[Property]:
    """Rotates which property leads among a comparable-price band at the
    FRONT of an already price-ranked list, seeded off call_session_id so
    different calls with similar criteria don't all get the exact same
    top-of-portfolio property recommended first, every time (confirmed as
    the real mechanism live: sql_search orders strictly by
    Property.base_price.asc(), deterministic and identical across any
    number of different guests with the same budget/location/guest-count).
    Only ever reorders among options already within _COMPARABLE_PRICE_BAND
    of the cheapest -- a candidate set with only one real match, or where
    one candidate is a clearly better fit (outside the band), is returned
    completely unchanged. Never called on the combo-fallback path
    (orchestrator.py skips this when combo_note is set) -- that path's
    ordering already carries its own meaning (which units to pair), not a
    "pick one" recommendation.
    """
    if len(properties) < 2:
        return list(properties)

    cheapest_price = float(properties[0].base_price)
    if cheapest_price <= 0:
        # Can't compute a meaningful percentage band against a zero/invalid
        # price -- fail open to the existing order rather than dividing by
        # zero or guessing. (Phase 2.0 already excludes true base_price<=0
        # properties from candidates in the common case; this guards the
        # exact_airbnb_pricing=True exception to that exclusion, where
        # base_price can still legitimately be 0.)
        return list(properties)

    band_end = 1
    for property_ in properties[1:]:
        if (float(property_.base_price) - cheapest_price) / cheapest_price <= _COMPARABLE_PRICE_BAND:
            band_end += 1
        else:
            break

    if band_end < 2:
        return list(properties)

    seed = _rotation_seed(call_session_id)
    rotation = seed % band_end
    band = properties[:band_end]
    rotated_band = band[rotation:] + band[:rotation]
    return rotated_band + properties[band_end:]
