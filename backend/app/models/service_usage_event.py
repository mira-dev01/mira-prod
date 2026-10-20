import uuid

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class ServiceUsageEvent(UUIDPkMixin, TimestampMixin, Base):
    """One metered quantity of one paid external service -- the admin
    panel's usage/cost source of truth (app/services/usage_meter.py writes,
    app/services/admin_monitor_service.py reads). Written fail-open and
    never read back into a live call.

    In-call usage (LLM tokens, Sarvam STT seconds / TTS characters) is
    aggregated per call and written once at call end, one row per
    (service, unit, model); out-of-call usage (post-call LLM, WhatsApp,
    email, SearchApi, Bright Data, embeddings) is one row per request.
    Exotel minutes are deliberately NOT metered here -- they're derived from
    call_sessions durations at query time, the same data Exotel bills on.
    """

    __tablename__ = "service_usage_events"
    __table_args__ = (Index("ix_service_usage_events_service_created", "service", "created_at"),)

    service: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
