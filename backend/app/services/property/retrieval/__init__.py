"""recommend_properties' retrieval pipeline: filter -> SQL search ->
(conditionally) semantic search -> merge/rank -> build context. See
orchestrator.py for the actual pipeline wiring; each other module in this
package is a single, focused responsibility (filter_builder, sql_search,
semantic_search, ranking, context_builder, formatter).
"""

from app.services.property.retrieval.orchestrator import recommend_properties

__all__ = ["recommend_properties"]
