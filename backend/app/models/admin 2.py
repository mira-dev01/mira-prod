from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class AdminLoginCode(UUIDPkMixin, TimestampMixin, Base):
    """One emailed one-time login code for the internal /admin panel (see
    app/api/v1/admin_auth.py). Only an HMAC of the code is stored; a row is
    single-use (consumed_at) and attempt-limited."""

    __tablename__ = "admin_login_codes"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminServiceSetting(UUIDPkMixin, TimestampMixin, Base):
    """Operator-entered billing settings for one external service, edited
    from the admin panel's Usage page: a prepaid amount (for services with no
    balance API -- Sarvam, Exotel -- remaining = prepaid_amount minus metered
    spend since prepaid_set_at) and per-unit prices that turn metered usage
    into spend. unit_prices maps unit -> price per ONE unit, in `currency`."""

    __tablename__ = "admin_service_settings"

    service: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR", server_default="INR")
    prepaid_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    prepaid_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unit_prices: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(255))
