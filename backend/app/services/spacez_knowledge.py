"""SPACEZ ONLY. A Spacez villa's knowledge-base facts: its profile (bedrooms, guests, parties, pets, food) and
every rate and add-on price the agent may be asked about. Built from properties.pricing_terms and the
property's own fields, and returned to app/services/property/chunking.py's build_fact_chunks, so they land
in the same `property_chunks` fact rows `search_faq` reads -- the agent says nothing about a villa that is not
on file, and these are what is on file.

Returns [] for a property without Spacez terms, so no other property is affected.

Prices here are the same numbers the bill uses (app/services/spacez_stay_bill.py); they are written out so a
guest's "how much is the BBQ kit?" is answered from the knowledge base, while the stay total is always the
get_pricing bill. A coupon or offer is deliberately NOT a fact: the agent never answers one.
"""

from decimal import Decimal

from app.services import spacez_pricing_terms, spacez_stay_bill
from app.services.property.chunking import FactDraft

_ADDON_LABELS = {"bbq_experience": "BBQ Experience", "movie_kit": "Movie kit", "floating_kit": "Floating kit"}


def _r(value) -> str:
    return spacez_stay_bill.rupees(Decimal(str(value)))


def facts_for(property_) -> list[FactDraft]:
    terms = spacez_pricing_terms.terms_for(property_)
    if terms is None:
        return []
    cfg = spacez_pricing_terms.PricingTermsConfig.model_validate(property_.pricing_terms)
    villa = property_.display_name or property_.name
    facts: list[tuple[str, str]] = []

    where = cfg.area or property_.city
    if where:
        facts.append(("location", f"Location: {villa} is in {where}."))
    if property_.bedroom_count:
        facts.append(("bedrooms", f"{villa} has {property_.bedroom_count} bedrooms."))
    facts.append(
        (
            "guests",
            f"Guests: the rate includes {terms.base_guests} guests and {villa} sleeps up to {terms.max_guests}. Each guest "
            f"above {terms.base_guests} is {_r(spacez_stay_bill.EXTRA_GUEST_PER_NIGHT)} per night. Children (ages 2 to 12) are "
            "free until the group goes over the included guests, then each child costs the same as an adult. Pets are never "
            "counted as guests.",
        )
    )
    if cfg.parties:
        facts.append(("parties", f"Parties: {cfg.parties}."))
    if cfg.pets:
        facts.append(("pets", f"Pets: {cfg.pets}."))
    if cfg.food:
        facts.append(("food", f"Food: {cfg.food}."))

    if terms.weekend_rate is not None and terms.weekend_rate != terms.base_rate:
        facts.append(
            (
                "rates",
                f"Rates: {_r(terms.base_rate)} per night on weeknights; Friday and Saturday nights are {_r(terms.weekend_rate)} "
                "per night. Rates are per night, before GST.",
            )
        )
    else:
        facts.append(("rates", f"Rates: {_r(terms.base_rate)} per night, the same on every night. Rates are per night, before GST."))
    facts.append(
        (
            "gst",
            "GST: 5% when the total reservation amount per night is up to ₹7,500, and 18% above that. The Spacez service fee "
            "is waived.",
        )
    )

    if terms.partner:
        facts.append(("meals", f"{villa} is a partner villa: Spacez meals and welcome drinks are not offered, and the booking is paid in full."))
    else:
        breakfast = "included in the stay" if terms.breakfast_included else f"{_r(terms.breakfast_price)} per adult per night"
        facts.append(
            (
                "meals",
                f"Meals: breakfast is {breakfast}; lunch is {_r(terms.lunch_price)} and dinner is {_r(terms.dinner_price)} per adult "
                f"per night. Welcome drinks and snacks are {_r(terms.welcome_price)} per guest.",
            )
        )
        if terms.meal_bundle_price is not None:
            facts.append(("bundle", f"Meal bundle (lunch and dinner together, with breakfast): {_r(terms.meal_bundle_price)} per adult per night."))

    addons = [f"{_ADDON_LABELS.get(k, k.replace('_', ' ').title())} {_r(v)} per night" for k, v in terms.addons.items()]
    if addons:
        facts.append(("addons", "Add-ons: " + ", ".join(addons) + f". Party platter {_r(terms.party_platter_price)} per adult."))
    else:
        facts.append(("addons", f"Add-ons: party platter {_r(terms.party_platter_price)} per adult."))
    if terms.pickup_fare is not None:
        facts.append(("pickup", f"Pickup and drop: a private car with chauffeur for {_r(terms.pickup_fare)}, taxes included."))
    facts.append(
        (
            "early_late",
            f"Early check-in is a flat {_r(terms.early_checkin_fee)} and late check-out is a flat {_r(terms.late_checkout_fee)}. "
            "Both depend on the villa being free, and the team confirms them.",
        )
    )
    return [FactDraft(text, "structured", f"spacez_{ref}") for ref, text in facts]
