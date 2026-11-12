"""SPACEZ ONLY. The extra rules the voice agent gets when the host has Spacez villas (properties with
`pricing_terms`). Returns "" for any other host, so nothing about a normal property's prompt changes.

The behaviour itself lives in code, not in this text: the bill (app/services/spacez_stay_bill.py), the
early/late request (app/services/spacez_stay_requests.py) and the coupon refusal (tool_handlers.py). This text
only tells the model WHEN to use them and what to say, and it states no price, fee or policy -- every number
comes from a tool result.
"""

from app.services import spacez_pricing_terms


def has_spacez_villa(properties) -> bool:
    return any(spacez_pricing_terms.terms_for(p) is not None for p in properties or [])


def spacez_section(properties) -> str:
    if not has_spacez_villa(properties):
        return ""
    return (
        "\nSPACEZ BOOKINGS (villas that quote a full bill):\n"
        "- When you quote a stay (get_pricing), say ONLY the room price per night and the ONE total for the stay that the "
        "result gives you, for the number of guests the guest finalised. Do not read out the line-by-line bill. Then tell "
        "the guest you will share the payment link with them on WhatsApp shortly, with the bill summary.\n"
        "- Pass the guest's choices to get_pricing so the total fits THEIR stay: how many are children, pets, which meals, "
        "welcome drinks, party platters, add-on kits, pickup and drop. Include only what the guest asked for. Set "
        "requested_early_checkin / requested_late_checkout ONLY if the guest themselves asked for it; then it is part of "
        "the total and of the bill summary.\n"
        "- COUPONS, BANK OFFERS AND DISCOUNTS: never answer them. Do not say whether one exists, applies or is possible. "
        "Say the team will get back to them about it, and call escalate_to_host with their request as the reason.\n"
        "- A guest who ALREADY HAS a confirmed booking and asks for an early check-in or a late check-out: call "
        "request_early_late_checkout (the number they are calling from is used to find the booking). Tell them the charge "
        "it gives you, then say you will let the team check the possibility, and if it is possible you will share the "
        "payment link with them on WhatsApp. Never say it is confirmed or promise it. If the team cannot do it, the guest "
        "is told on WhatsApp.\n"
    )
