"""SPACEZ ONLY. A villa's commercial terms for the full booking bill, read from properties.pricing_terms.

Stored as a small JSON object; every key is optional and an absent key means "not on file" (for the
things a bill cannot guess) or the documented default (meal prices). Unknown stays unknown: see
app/services/spacez_stay_bill.py.

    {"base_guests": 8, "partner": false, "breakfast_included": false,
     "meal_bundle_price": null, "pickup_fare": null,
     "addons": {"bbq_experience": 1500}, "breakfast_price": 250, ...,
     "parties": "small gatherings only", "pets": "allowed", "food": "Veg and non-veg"}

The weekend rate is NOT in this JSON. It is the property's own "weekend rate differs" toggle
(properties.weekend_rate_enabled / weekend_price, set in the dashboard like any other property setting);
a villa with it off charges the weeknight rate on Friday and Saturday nights too.

The `parties` / `pets` / `food` / `area` keys are the villa's profile from the Spacez knowledge base. They
do not touch the bill: app/services/spacez_knowledge.py turns them (and the prices) into the property's
knowledge-base facts so the agent can answer a guest's questions from what is on file.
"""

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services import spacez_stay_bill


class PricingTermsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_guests: int = Field(ge=1, le=100)
    partner: bool = False
    breakfast_included: bool = False
    meal_bundle_price: float | None = Field(default=None, ge=0)
    pickup_fare: float | None = Field(default=None, ge=0)
    breakfast_price: float | None = Field(default=None, ge=0)
    lunch_price: float | None = Field(default=None, ge=0)
    dinner_price: float | None = Field(default=None, ge=0)
    welcome_price: float | None = Field(default=None, ge=0)
    party_platter_price: float | None = Field(default=None, ge=0)
    early_checkin_fee: float | None = Field(default=None, ge=0)
    late_checkout_fee: float | None = Field(default=None, ge=0)
    addons: dict[str, float] = Field(default_factory=dict)
    # Knowledge-base profile (free text, spoken only via search_faq -- see spacez_knowledge.py).
    parties: str | None = Field(default=None, max_length=120)
    pets: str | None = Field(default=None, max_length=120)
    food: str | None = Field(default=None, max_length=160)
    area: str | None = Field(default=None, max_length=160)


def _d(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def weekend_rate_for(property_) -> Decimal | None:
    """The villa's Friday/Saturday nightly rate when the host turned "weekend rate differs" on and gave
    one; None otherwise (weekend nights then use the weeknight rate)."""
    if not getattr(property_, "weekend_rate_enabled", False):
        return None
    price = _d(getattr(property_, "weekend_price", None))
    return price if price is not None and price > 0 else None


def terms_for(property_) -> spacez_stay_bill.Terms | None:
    """The villa's terms, or None when it has none (it keeps the flat base_price quote). A stored value
    that no longer validates counts as none rather than raising into a live call."""
    stored = getattr(property_, "pricing_terms", None)
    if not stored:
        return None
    try:
        cfg = PricingTermsConfig.model_validate(stored)
    except ValidationError:
        return None
    if not property_.base_price or Decimal(str(property_.base_price)) <= 0:
        return None  # no weeknight rate on file: nothing to build a bill from
    defaults = spacez_stay_bill.Terms(base_rate=Decimal(1), base_guests=1, max_guests=1)
    return spacez_stay_bill.Terms(
        base_rate=Decimal(str(property_.base_price)),
        base_guests=cfg.base_guests,
        max_guests=property_.max_guests,
        weekend_rate=weekend_rate_for(property_),
        partner=cfg.partner,
        breakfast_included=cfg.breakfast_included,
        breakfast_price=_d(cfg.breakfast_price) or defaults.breakfast_price,
        lunch_price=_d(cfg.lunch_price) or defaults.lunch_price,
        dinner_price=_d(cfg.dinner_price) or defaults.dinner_price,
        welcome_price=_d(cfg.welcome_price) or defaults.welcome_price,
        party_platter_price=_d(cfg.party_platter_price) or defaults.party_platter_price,
        addons={name: Decimal(str(price)) for name, price in cfg.addons.items()},
        meal_bundle_price=_d(cfg.meal_bundle_price),
        pickup_fare=_d(cfg.pickup_fare),
        early_checkin_fee=_d(cfg.early_checkin_fee) or defaults.early_checkin_fee,
        late_checkout_fee=_d(cfg.late_checkout_fee) or defaults.late_checkout_fee,
    )
