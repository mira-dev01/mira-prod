import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PolicyDocument(UUIDPkMixin, TimestampMixin, Base):
    """A file a host uploaded for Mira to read. Only the facts about the upload
    are kept (what, how big, how many policies came out) -- never the file, so
    a booking-terms PDF with guest or bank details is not stored here."""

    __tablename__ = "policy_documents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # True when the file was longer than Mira reads in one go.
    truncated: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")


class Policy(UUIDPkMixin, TimestampMixin, Base):
    """One booking/house policy in the host's own words, e.g. "Pets are not
    allowed". property_id NULL means it applies to the whole portfolio; a row
    with a property_id replaces the portfolio row of the same topic for that
    property only (see app/services/policy_service.py::effective).

    status "draft" = read from a document, waiting for the host; only "active"
    rows reach the agent's knowledge base."""

    __tablename__ = "policies"
    __table_args__ = (
        # One active policy per topic per scope (drafts may pile up beside it).
        # "other" is the free-form topic and may repeat.
        Index(
            "ix_policies_portfolio_topic",
            "user_id",
            "topic",
            unique=True,
            postgresql_where=text("property_id IS NULL AND status = 'active' AND topic <> 'other'"),
        ),
        Index(
            "ix_policies_property_topic",
            "property_id",
            "topic",
            unique=True,
            postgresql_where=text("property_id IS NOT NULL AND status = 'active' AND topic <> 'other'"),
        ),
        Index("ix_policies_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_documents.id", ondelete="SET NULL")
    )

    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # draft | active
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active", server_default="active")
    # The wording in the uploaded file this was read from, so the host can
    # check it against the original.
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    # Things the host should look at before approving ("couldn't find this
    # wording in the document", "mentions a property we couldn't match").
    review_notes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
