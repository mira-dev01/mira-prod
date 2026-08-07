import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PropertyPricingRule(UUIDPkMixin, TimestampMixin, Base):
    """A host's pricing ideology (minimum-stay rules, length-of-stay
    discounts, early check-in/late checkout fees, freeform concessions),
    broken into structured placeholders by
    pricing_policy_service.parse_pricing_policy_text -- either typed or
    dictated (DictationTextarea's existing mic -> POST /voice/transcribe ->
    Sarvam batch STT flow, unchanged, no new transcription code needed) --
    and reviewed/approved by the host in the AI Training tab before
    pricing_engine.py ever reads it. Same lifecycle as HostDiscountRule
    (pending_validation -> approved/rejected), same source tracking
    (ai_parsed/host_edited).

    Host-scoped, not property-scoped, unlike PricingRule -- a single rule
    (e.g. "Saturdays need a 2-night minimum") can apply to many properties
    at once via the host's own checkbox selection in the UI. property_ids is
    a plain JSONB array of the selected Property.id values rather than a
    second join table -- one new table for this feature, not two; matches
    how PricingRule.condition already stores structured data as JSONB
    rather than normalized columns. Deliberately NOT folded into the
    existing PricingRule (property_id there is a required FK, one row per
    property -- explicitly rejected in favor of this table so a host edits
    one rule instead of N duplicated rows) or HostDiscountRule (a different
    concept -- negotiation-time discount policy, not a stay-price/fee rule).
    """

    __tablename__ = "property_pricing_rules"

    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    # "length_of_stay" / "minimum_stay_nights" / "early_checkin_fee" /
    # "late_checkout_fee" / "custom" -- see pricing_engine.py for which of
    # these it actually reads and how.
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Free-form, same shape as PricingRule.condition -- e.g.
    # {"min_nights": 5} for length_of_stay, {"weekend_min_nights": 2} for
    # minimum_stay_nights, {"fee": 1500} for early_checkin_fee/late_checkout_fee.
    condition: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Nullable -- only discount-shaped rules (length_of_stay, custom) use
    # this; fee-shaped rules (early_checkin_fee, late_checkout_fee) carry
    # their amount in condition["fee"] instead, since a fee is a flat rupee
    # add-on, not a percentage off.
    discount_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))
    # Plain-English description for a "custom" rule whose wording didn't map
    # onto one of the known rule_types -- same escape hatch as
    # HostDiscountRule.trigger_type="custom", but here the type itself stays
    # "custom" and this field carries the host's own phrasing since a
    # freeform concession has no natural short rule_type label.
    label: Mapped[str | None] = mapped_column(String(255))
    property_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String(16), default="ai_parsed", server_default="ai_parsed")
    status: Mapped[str] = mapped_column(String(32), default="pending_validation", server_default="pending_validation")
    raw_source_text: Mapped[str | None] = mapped_column(Text)

    host: Mapped["User"] = relationship(back_populates="property_pricing_rules")
