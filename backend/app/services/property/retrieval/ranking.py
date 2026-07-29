"""Merges SQL and (optional) semantic search candidates into one ordered
list. Deliberately a simple weighted union, not a learned ranker -- SQL
order (price ascending, today's behavior) is always primary/authoritative;
a semantic-only match is appended after, never inserted ahead of a hard
SQL match. Future extension point: swap this module's internals for a
cross-encoder/LLM reranker without touching any other module in this
package -- the list[Property] -> list[Property] signature is stable
regardless of internal sophistication.
"""

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
