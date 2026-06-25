import uuid

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PricingRule(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "pricing_rules"

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE")
    )
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. loyalty_discount, occupancy_discount
    condition: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    property: Mapped["Property"] = relationship(back_populates="pricing_rules")
