import builtins
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Notification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="CASCADE")
    )
    # Nullable, set only by recovery_service.py/whatsapp_reply_service.py's
    # busy_recovery/busy_recovery_reply notifications (see app/models/lead.py's
    # recovery_reason) -- gives Recovery Analytics (docs/analytics.md-equivalent:
    # app/api/v1/analytics.py's recovery endpoint) a reliable join back to the
    # specific recovery Lead a notification belongs to. Every other channel
    # (whatsapp/escalation/system) leaves this NULL; nothing about the general
    # Notification read/write path changes.
    # Indexed: the join key every GET /analytics/recovery query uses to
    # correlate a notification back to its recovery Lead (4 separate queries
    # per page load, see app/api/v1/analytics.py's analytics_recovery) --
    # without an index this forces a sequential scan over the whole table on
    # every load, worsening as notifications accumulate.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), index=True
    )
    # Indexed: filtered by every query in this file that scopes to one
    # channel (escalated_calls in analytics_summary/analytics_timeseries,
    # and all of analytics_recovery's busy_recovery/busy_recovery_reply
    # queries) -- same reasoning as lead_id above.
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # whatsapp | escalation | system
    urgency: Mapped[str] = mapped_column(String(16), default="low", server_default="low")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")
    # Set once, the first time this notification is marked read (see
    # notification_service.mark_read) -- never overwritten on subsequent
    # reads. This is the "host responded" signal Recovery Analytics' Average
    # Host Response metric uses; Notification.updated_at is NOT reused for
    # this because it's a generic mixin field that would also move on any
    # unrelated future mutation, not a purpose-built response timestamp.
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped["Property"] = relationship(back_populates="notifications")
    call_session: Mapped["CallSession"] = relationship(back_populates="notifications")

    # builtins.property, not the bare @property decorator -- the
    # relationship above is itself named `property`, which shadows the
    # decorator name within this class body (bare @property here resolves
    # to that relationship's descriptor, not the builtin, and fails with
    # "'_RelationshipDeclared' object is not callable").
    @builtins.property
    def property_name(self) -> str | None:
        # Computed, not stored -- the Live Requests panel needs a
        # human-readable property name (hosts are non-technical, a raw
        # property_id means nothing to them), not just property_id.
        # Requires the caller to eager-load `property` (see
        # notification_service.list_notifications) since this can run after
        # the async session that fetched the row has already closed.
        return self.property.name if self.property_id and self.property else None
