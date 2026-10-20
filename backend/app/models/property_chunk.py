import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PropertyChunk(UUIDPkMixin, TimestampMixin, Base):
    """One chunk of a property's text, embedded separately by chunk_type
    (overview/amenities/location/house_rules/reviews) rather than one
    embedding for the whole property -- keeps a semantic search scoped to
    "location" chunks (say) more precise than matching against one giant
    blended vector. Generated at import time from fields the parsers/
    normalizer already extracted (see _upsert_property_from_parsed in
    app/api/v1/properties.py) -- not a separate post-import job, since the
    data is already in hand there.

    embedding is a plain JSONB float array, same pattern as
    FaqEntry.question_embedding (app/models/faq_entry.py) -- not pgvector,
    for the same reason: production DB extension availability can't be
    verified from this environment, and the comparison set (one host's
    chunks) is small enough for in-Python cosine similarity to be cheap.
    Computed fire-and-forget after the import response returns (see
    embedding_service.backfill_property_chunk_embedding) -- never blocks
    an import, and null here just means this chunk has no semantic match
    possible yet, same fail-open stance as everywhere else embeddings are
    used in this codebase.
    """

    __tablename__ = "property_chunks"

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    # Plain string, not an enum -- matches this codebase's general
    # preference for plain strings over DB enums (no enum type used
    # anywhere else in property.py). "reviews" is a reserved-but-currently-
    # unpopulated type -- no review text exists in the scrape schema yet.
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list | None] = mapped_column(JSONB)

    property: Mapped["Property"] = relationship(back_populates="chunks")
