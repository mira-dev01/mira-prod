from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class GuestProfile(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "guest_profiles"

    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    total_stays: Mapped[int] = mapped_column(default=0, server_default="0")
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    notes: Mapped[str | None] = mapped_column(Text)

    call_sessions: Mapped[list["CallSession"]] = relationship(back_populates="guest_profile")
