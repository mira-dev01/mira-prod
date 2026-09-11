import uuid

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class CallQualityEvent(UUIDPkMixin, TimestampMixin, Base):
    """Persisted copy of one ValidationResult (app/voice/conversation_quality.py)
    recorded during a call. Written once, after the call ends, from
    on_pipeline_finished (app/voice/pipeline.py) -- never read back into a
    live call. Purely for cross-call aggregation/analytics (see
    call_service.record_quality_events and the docs/tasks/
    building-intelligence.md task list this belongs to); does not change
    ConversationQuality's own in-memory, per-call, non-persisted behavior.
    """

    __tablename__ = "call_quality_events"
    __table_args__ = (
        Index("ix_call_quality_events_call_session_id", "call_session_id"),
        Index("ix_call_quality_events_rule_severity", "rule", "severity"),
    )

    call_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_sessions.id", ondelete="CASCADE")
    )

    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_time_ms: Mapped[float] = mapped_column(Float, nullable=False)
    # Mirrors ValidationResult.metadata -- not named `metadata`, which is a
    # reserved attribute name on SQLAlchemy declarative models.
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
