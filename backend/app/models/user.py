from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(32), default="tier_1", server_default="tier_1")
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")

    properties: Mapped[list["Property"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
