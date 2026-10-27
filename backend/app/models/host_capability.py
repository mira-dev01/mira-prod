import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class HostCapability(UUIDPkMixin, TimestampMixin, Base):
    """A host's stored choice for one capability in the registry
    (app/services/capability_registry.py). Only "preference" capabilities
    read `enabled` from here -- "core" capabilities are always on and
    "bound" ones (e.g. negotiation) take their state from the existing
    User column they're bound to, so this row never becomes a second,
    competing source of truth for them. A missing row means "the
    registry's default", which is chosen per capability to match how the
    app behaved before capabilities existed -- existing hosts need no
    backfill.

    `capability_id` is a registry id, deliberately not an enum/FK: the
    registry is code, and an id the current registry no longer knows is
    simply ignored on read rather than breaking the host's settings page.
    """

    __tablename__ = "host_capabilities"
    __table_args__ = (UniqueConstraint("user_id", "capability_id", name="uq_host_capabilities_user_capability"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # not_started | in_progress | complete | deferred -- the host's own
    # bookkeeping of setup ("set this up later" in onboarding), separate
    # from readiness, which is always recomputed from real data.
    setup_state: Mapped[str] = mapped_column(String(16), nullable=False, default="not_started", server_default="not_started")
    # Capability-specific settings that have no existing home. Keys are
    # allowlisted per capability by the registry's config_keys.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
