import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class NegotiationRule(UUIDPkMixin, TimestampMixin, Base):
    """A host's single, unified negotiation/pricing training policy --
    replaces what used to be two separate tables (HostDiscountRule,
    trigger-based negotiation discounts; PropertyPricingRule, stay-pricing
    rules like minimum stay/length-of-stay/fees). Hosts were describing both
    to Mira in their own words as one mental model ("how should the agent
    negotiate"), so this merges them into one table, one parse prompt, one
    training UI -- pricing_engine.py reads a single source instead of two.

    rule_type is one of the six kinds either predecessor supported:
    "discount_no_ask" / "discount_guest_requests" / "discount_repeat_guest"
    (host-wide negotiation triggers, formerly HostDiscountRule.trigger_type)
    or "length_of_stay" / "minimum_stay_nights" / "early_checkin_fee" /
    "late_checkout_fee" / "custom" (formerly PropertyPricingRule.rule_type).
    condition carries type-specific structured data exactly as
    PropertyPricingRule did (e.g. {"min_nights": N}, {"fee": N}); discount
    triggers store their percent directly on discount_percent with an empty
    condition.

    property_ids is a JSONB array of Property.id values the rule applies
    to, same as PropertyPricingRule -- an EMPTY list means host-wide (every
    property), which is how the three discount trigger types (host-wide by
    definition under the old HostDiscountRule model) round-trip losslessly
    into this table: a migrated discount_no_ask/guest_requests/repeat_guest
    row simply gets property_ids=[]. Stay-pricing rule types keep requiring
    an explicit, host-picked non-empty subset before they take effect,
    exactly as PropertyPricingRule did -- this table doesn't change that
    per-type behavior, only where the data lives.

    stages (Phase 4D, generalized negotiation policy model -- see
    documentation design docs "Phase 4B: Generalized Negotiation Policy
    Model" and "Phase 4C: Negotiation Semantics Contract"): an OPTIONAL,
    nullable, ordered list of {"order": int, "value": float} objects
    representing a host-defined negotiation ladder for THIS rule, e.g.
    [{"order": 0, "value": 5}, {"order": 1, "value": 8}] -- arbitrary
    length, arbitrary values, entirely host-configured; no code anywhere
    reads a hardcoded stage count or stage value. NULL (the default, and
    every existing row's value) means "no ladder -- use discount_percent
    exactly as before," making this purely additive: a host who never
    configures a staged policy sees zero behavior change, and the flat
    discount_percent column keeps its existing meaning unchanged whether or
    not stages is populated. Per the ratified Phase 4C decision, when BOTH
    are set on approved rules of the same rule_type/scope, stages takes
    precedence (see app/services/negotiation_policy.py's
    resolve_staged_or_flat) -- discount_percent is never merged with a
    populated stages list on the same or a competing row.
    """

    __tablename__ = "negotiation_rules"

    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    condition: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    discount_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))
    label: Mapped[str | None] = mapped_column(String(255))
    property_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String(16), default="ai_parsed", server_default="ai_parsed")
    status: Mapped[str] = mapped_column(String(32), default="pending_validation", server_default="pending_validation")
    raw_source_text: Mapped[str | None] = mapped_column(Text)
    stages: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True, default=None)

    host: Mapped["User"] = relationship(back_populates="negotiation_rules")
