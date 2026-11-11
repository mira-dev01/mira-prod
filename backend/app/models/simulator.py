"""State for the test services (app/integrations/payment_simulator.py). Only ever written when
SIMULATOR_ENABLED is on; the tables exist everywhere but stay empty."""

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class SimPaymentLink(TimestampMixin, Base):
    """A pretend payment link. status: created | paid | cancelled | expired. A failed attempt leaves the
    link `created` (the guest can try again), exactly as a real gateway does, and is remembered in
    last_attempt."""

    __tablename__ = "sim_payment_links"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    amount_paise: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created", server_default="created")
    last_attempt: Mapped[str | None] = mapped_column(String(16))  # failed | None
    payment_id: Mapped[str | None] = mapped_column(String(48))


class SimulatorFlag(TimestampMixin, Base):
    """A switch: "payment", "messaging" or "calendar" is down."""

    __tablename__ = "simulator_flags"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class SimMessage(UUIDPkMixin, TimestampMixin, Base):
    """A WhatsApp message the simulator "sent" (or refused), kept so a tester can read what a guest got."""

    __tablename__ = "sim_messages"

    to_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # sent | failed
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="text", server_default="text")
