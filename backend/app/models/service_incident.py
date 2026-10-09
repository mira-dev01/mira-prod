import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class ServiceIncident(UUIDPkMixin, TimestampMixin, Base):
    """One period a monitored service spent degraded/down (app/services/
    health_monitor_service.py writes, the /admin home page reads). Live state
    itself is in-memory (app/observability/health.py); this table is the
    durable part: incident history, the 24h availability bars, and alert
    dedupe that survives a restart/redeploy. Scoped per `environment` in
    case dev and production ever share a database."""

    __tablename__ = "service_incidents"
    __table_args__ = (Index("ix_service_incidents_env_service_open", "environment", "service", "resolved_at"),)

    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # worst state reached: degraded | down
    current_state: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_error: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sample_call_session_id: Mapped[str | None] = mapped_column(String(64))
    down_alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    degraded_alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    alerts_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    errors_at_last_alert: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
