import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class DemoCallRequest(UUIDPkMixin, TimestampMixin, Base):
    """One visitor's "test the agent" call from the marketing site.

    The row is the lead (name + number the visitor gave), the single-use grant
    for the call (`started_at` is set when the call is allowed to start, so a
    token cannot start a second one) and the record the rate limits count over.
    The visitor's IP is stored only as a salted hash: enough to limit repeats,
    not enough to identify anyone.
    """

    __tablename__ = "demo_call_requests"
    __table_args__ = (
        Index("ix_demo_call_requests_phone_created", "phone", "created_at"),
        Index("ix_demo_call_requests_ip_created", "ip_hash", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
