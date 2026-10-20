import uuid

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class Technician(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "technicians"

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialty: Mapped[str] = mapped_column(String(32), nullable=False)  # plumbing/electrical/ac/wifi/lock/general
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    rating: Mapped[float] = mapped_column(Numeric(2, 1), default=5.0, server_default="5.0")

    property: Mapped["Property"] = relationship(back_populates="technicians")
