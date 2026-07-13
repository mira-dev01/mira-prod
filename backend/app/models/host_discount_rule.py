import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class HostDiscountRule(UUIDPkMixin, TimestampMixin, Base):
    """A host's discount policy, broken into structured placeholders by
    POST /auth/me/discount-policy/parse (LLM extraction from
    User.discount_policy_text's free text), reviewed/approved by the host in
    the AI Training validation tab before pricing_engine.negotiate_rate ever
    reads it (see memory-architecture-plan.md section 4). Derive-on-read,
    not materialized per property -- pricing_engine looks these up by
    host_id directly rather than duplicating a row per property, so editing
    one rule immediately applies everywhere without drift.
    """

    __tablename__ = "host_discount_rules"

    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    # "no_ask" / "guest_requests" / "repeat_guest_same_host" / a custom label
    # the host's own wording didn't map cleanly onto the three built-ins.
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai_parsed", server_default="ai_parsed")
    status: Mapped[str] = mapped_column(String(32), default="pending_validation", server_default="pending_validation")
    raw_source_text: Mapped[str | None] = mapped_column(Text)

    host: Mapped["User"] = relationship(back_populates="discount_rules")
