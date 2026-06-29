import uuid
from builtins import property as python_property
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class CallSession(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "call_sessions"

    exotel_call_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # Always set when the host is known at call time (every voice-pipeline
    # path resolves one), independent of property_id -- Lead Agent calls
    # legitimately have no single property, but still belong to a host.
    # Dashboard queries (calls/guests/analytics) must scope by this, not by
    # property ownership, or Lead Agent calls become invisible (property_id
    # is never IN any property-id list when it's NULL).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL")
    )
    guest_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guest_profiles.id", ondelete="SET NULL")
    )
    caller_number: Mapped[str | None] = mapped_column(String(32))
    recording_url: Mapped[str | None] = mapped_column(String(1024))
    transcript: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="in_progress", server_default="in_progress")
    urgency: Mapped[str | None] = mapped_column(String(16))
    revenue_attributed: Mapped[float] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped["Property"] = relationship(back_populates="call_sessions")
    guest_profile: Mapped["GuestProfile"] = relationship(back_populates="call_sessions")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="call_session")
    lead: Mapped["Lead"] = relationship(back_populates="call_session")

    # Plain @property breaks here -- the `property` relationship column
    # above shadows the builtin `property` decorator for the rest of this
    # class body, since a Python class body is just sequential execution
    # in a shared namespace. Use the aliased import instead.
    @python_property
    def duration_minutes(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at).total_seconds() / 60, 1)

    @python_property
    def guest_name(self) -> str | None:
        # The Lead row (filled in via the update_lead tool during the call)
        # is the more reliable source -- a guest profile only has a name if
        # they're a repeat caller whose profile was edited separately.
        if self.lead is not None and self.lead.guest_name:
            return self.lead.guest_name
        if self.guest_profile is not None:
            return self.guest_profile.name
        return None

    @python_property
    def guest_phone(self) -> str | None:
        # caller_number is the raw signaling-level identity (e.g.
        # "browser-test" for in-dashboard tests, or absent if the agent
        # never confirmed it out loud) -- the Lead's phone field is what the
        # guest actually said during the call, which is what a host wants
        # to see and call back.
        if self.lead is not None and self.lead.phone:
            return self.lead.phone
        return self.caller_number
