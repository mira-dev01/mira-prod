"""Thin re-export of Phase 3's render_recommendation_text -- this file
exists to match the retrieval/ package's requested layout, not to hold new
logic. The actual formatting lives in app/services/property/
pitch_formatter.py, built during Phase 3 and reused here rather than
duplicated.
"""

from app.services.property.pitch_formatter import render_recommendation_text

__all__ = ["render_recommendation_text"]
