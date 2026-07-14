import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class GuestProfile(UUIDPkMixin, TimestampMixin, Base):
    """Guest Memory (memory-architecture-plan.md section 1) -- cross-call,
    per-host continuity for a returning caller. Deliberately does NOT
    duplicate Lead.status/lead_temperature/occasion (those stay
    single-sourced per Lead, one per call); this table aggregates *across*
    a guest's calls to one host instead. Never stores raw transcripts --
    only short, host-facing summaries, same pattern as the
    escalate_to_host email (see conversation_summaries below).
    """

    __tablename__ = "guest_profiles"
    __table_args__ = (UniqueConstraint("phone", "host_id", name="uq_guest_profiles_phone_host"),)

    # host_id is nullable at the column level only because a handful of
    # pre-Guest-Memory rows may already exist without one (created before
    # this migration) -- every NEW row must always set it (see
    # call_service.get_or_create_guest_profile). Composite-uniqued with
    # phone so the same phone number calling two different hosts on Mira
    # gets two independent profiles, never one shared/leaked across hosts.
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    total_stays: Mapped[int] = mapped_column(default=0, server_default="0")
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    notes: Mapped[str | None] = mapped_column(Text)

    last_property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL")
    )
    # Dominant language/code-mixing pattern observed across calls (e.g.
    # "hindi", "english", "hinglish") -- inferred at call-end from the
    # transcript, not guest-declared.
    preferred_language: Mapped[str | None] = mapped_column(String(32))
    last_outcome: Mapped[str | None] = mapped_column(String(64))
    last_follow_up: Mapped[str | None] = mapped_column(String(255))
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Short, LLM-free summaries pulled directly from each call's own Lead
    # row (Lead.conversation_summary, already written by the voice agent's
    # update_lead tool -- see tool_handlers.py) -- never a new summarization
    # LLM call, and never the raw transcript. Each entry:
    # {call_session_id, property_id, property_name, date, summary,
    #  lead_temperature}. Capped in the write path (see call_service) so
    # this can't grow unbounded for a very long-tenured repeat guest.
    conversation_summaries: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    host: Mapped["User"] = relationship()
    last_property: Mapped["Property"] = relationship()
    call_sessions: Mapped[list["CallSession"]] = relationship(back_populates="guest_profile")
