from sqlalchemy import String, Text
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

    lead_exophone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)

    # Per-host voice agent customization (see app/prompts/system_prompt.py).
    # All optional -- None means "use Mira's default". agent_first_message
    # supports {host_name}, {property_name}, {city}, {guest_name} placeholders;
    # any placeholder that doesn't apply to the current call (e.g.
    # {property_name} on a Lead Agent call with no property selected yet)
    # resolves to "" rather than raising.
    agent_first_message: Mapped[str | None] = mapped_column(Text)
    agent_persona: Mapped[str | None] = mapped_column(Text)
    agent_escalation_phrase: Mapped[str | None] = mapped_column(Text)

    properties: Mapped[list["Property"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    leads: Mapped[list["Lead"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    faq_entries: Mapped[list["FaqEntry"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
