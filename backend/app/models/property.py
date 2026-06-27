import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Property(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "properties"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    exophone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    ical_url: Mapped[str | None] = mapped_column(String(1024))

    house_rules: Mapped[str | None] = mapped_column(Text)
    faq: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    amenities: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    check_in_time: Mapped[str] = mapped_column(String(8), default="14:00", server_default="14:00")
    check_out_time: Mapped[str] = mapped_column(String(8), default="11:00", server_default="11:00")
    max_guests: Mapped[int] = mapped_column(default=4, server_default="4")

    owner: Mapped["User"] = relationship(back_populates="properties")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    call_sessions: Mapped[list["CallSession"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    technicians: Mapped[list["Technician"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    pricing_rules: Mapped[list["PricingRule"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
