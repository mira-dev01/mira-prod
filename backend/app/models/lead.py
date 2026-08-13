import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Lead(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    # The call that ORIGINALLY created this lead -- stays 1:1/unique, never
    # repointed. A later call from the same returning guest may reuse this
    # same Lead instead of creating a new one (see lead_service.py's reuse
    # logic) via CallSession.lead_id, so this column no longer means "the
    # only call this lead is associated with" -- just "where it was born."
    # No relationship object here since nothing in the app reads
    # lead.call_session; CallSession.lead is the one direction actually
    # used, navigated via lead_id instead.
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"), unique=True
    )
    # Links to Guest Memory (memory-architecture-plan.md section 1) --
    # GuestProfile aggregates across this guest's leads for this host
    # rather than re-storing status/lead_temperature/occasion itself.
    # Nullable: not every lead has a resolvable caller identity (e.g. no
    # caller_number, or a call where the guest never gave a phone number).
    guest_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guest_profiles.id", ondelete="SET NULL"), index=True
    )

    guest_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    check_in: Mapped[date | None] = mapped_column(Date)
    check_out: Mapped[date | None] = mapped_column(Date)
    num_guests: Mapped[int | None] = mapped_column()
    purpose_of_stay: Mapped[str | None] = mapped_column(String(255))
    budget: Mapped[float | None] = mapped_column(Numeric(10, 2))
    preferred_location: Mapped[str | None] = mapped_column(String(255))

    properties_discussed: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    questions_asked: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    support_requests: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    # See app/voice/conversation_state.py's lifecycle-vocabulary cross-reference
    # (near ConversationGoal) for how this relates to Lead.status below and to
    # the in-call ConversationState.conversation_goal -- three separate,
    # unreconciled views of "how far along is this booking," not one field.
    lead_temperature: Mapped[str | None] = mapped_column(String(16))

    # --- Recovery/entry metadata (documentation/architecture: Phase 4) ---
    # Three separate axes, deliberately not merged into one field or reusing
    # lead_source for more than its original meaning:
    #   lead_source     -- WHAT SUBSYSTEM/FLOW created this row (voice_call,
    #                       manual entry, import, ...). Existed before this
    #                       phase; unchanged in meaning.
    #   entry_channel   -- HOW the guest reached Mira (phone_call today; a
    #                       future WhatsApp-inbound or web-widget lead would
    #                       set this to "whatsapp"/"web" while lead_source
    #                       stays whatever internal flow created the row).
    #   recovery_reason -- WHY this lead needed recovery/backfill instead of
    #                       coming from a normal completed conversation. Null
    #                       for the common case (a normal answered call that
    #                       captured its own lead data) -- only set when a
    #                       system-driven flow (not the live conversation
    #                       itself) is what produced/touched this lead.
    # None of these duplicate Lead.status: status is the sales-pipeline stage
    # (open/contacted/booked/closed), host-managed, and never describes how
    # or why a lead entered -- see the model's own status field below.
    lead_source: Mapped[str] = mapped_column(String(64), default="voice_call", server_default="voice_call")
    entry_channel: Mapped[str] = mapped_column(String(32), default="phone_call", server_default="phone_call")
    recovery_reason: Mapped[str | None] = mapped_column(String(32))

    # --- Busy-recovery availability follow-up (separate from both status
    # and recovery_reason above -- see app/services/recovery_service.py's
    # process_availability_recovery for the full state machine). Whether
    # Mira still owes this busy-recovery guest an "I'm available now"
    # WhatsApp message. Always null for a lead with no recovery_reason;
    # never read/written by anything sales-lifecycle-related (Kanban,
    # _REUSABLE_LEAD_STATUSES, host-facing status changes).
    busy_recovery_availability_status: Mapped[str | None] = mapped_column(String(16), index=True)
    # When THIS busy call happened -- not Lead.created_at/updated_at, see
    # migration 8f1c4b9e2a67's own comment for why those don't work here.
    busy_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set only while busy_recovery_availability_status == "processing";
    # lets a crashed worker's stuck claim become reclaimable after a short
    # staleness threshold instead of blocking that guest's notification
    # forever.
    busy_recovery_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation_summary: Mapped[str | None] = mapped_column(Text)
    next_follow_up: Mapped[str | None] = mapped_column(String(255))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    transferred_to_host: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Lifecycle status for the "Open Leads" overview card -- distinct from
    # lead_temperature (hot/warm/cold), which describes qualification, not
    # whether the host has actually followed up. Host-managed from the
    # Leads page; the voice agent never sets this.
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")

    # Free text, not an enum: guest phrasing for an occasion (birthday,
    # anniversary, honeymoon, ...) varies too much to bucket cleanly, and the
    # point is to capture verbatim what the guest said, not classify it.
    occasion: Mapped[str | None] = mapped_column(String(255))

    owner: Mapped["User"] = relationship(back_populates="leads")
