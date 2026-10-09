import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class HostOnboarding(UUIDPkMixin, TimestampMixin, Base):
    """Server-side onboarding progress, one row per host, so a host can
    refresh, close the browser or sign in on another device and resume
    exactly where they left off (see app/services/onboarding_service.py).

    A host with NO row is resolved by onboarding_service.get_or_infer:
    hosts who already have properties or a lead number are treated as
    onboarded (and get a "legacy" completed row) -- the migration that
    created this table also backfilled a completed row for every
    pre-existing user, so nobody is pushed back through onboarding.
    """

    __tablename__ = "host_onboarding"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # in_progress | completed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress", server_default="in_progress")
    # onboarding (new flow) | legacy (pre-capability host, backfilled)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="onboarding", server_default="onboarding")
    current_step: Mapped[str] = mapped_column(String(32), nullable=False, default="profile", server_default="profile")
    completed_steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    selected_capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # Step-specific data that must survive a refresh -- currently the first
    # property's import request/status (see onboarding_service).
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
