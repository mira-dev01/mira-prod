import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Booking(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("property_id", "source_uid", name="uq_booking_property_source_uid"),)

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    guest_phone: Mapped[str | None] = mapped_column(String(32))
    guest_name: Mapped[str | None] = mapped_column(String(255))
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="airbnb", server_default="airbnb")
    source_uid: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="confirmed", server_default="confirmed")

    property: Mapped["Property"] = relationship(back_populates="bookings")
