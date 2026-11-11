"""The WhatsApp confirmation a guest gets once a payment succeeds, in the booking flow's exact format,
and the four-digit stay code. Pure functions over a PaymentRequest's details.

Only for a request created from a full bill (details["bill"]); a plain payment link keeps the host's own
"payment received" template.
"""

import re
import secrets
from decimal import Decimal

from app.services.stay_bill import rupees

HEADER = "Hello {name}, Your Booking is now confirmed"
FOOTER = (
    "Spacez Food and Hospitality takes pleasure in promptly satisfying our guest's stay needs. Please\n"
    "leave a text here for any concern or requests.\n\n"
    "Regards,\nTeam Spacez\n\nwww.spacez.co"
)


def new_stay_code() -> str:
    return f"{secrets.randbelow(9000) + 1000}"


def booking_ref(request) -> str:
    """A short, readable booking id."""
    return f"SPZ-{request.id.hex[:6].upper()}"


def _day(value) -> str:
    return value.strftime("%d %b, %Y")


def _meal(bill: dict, label: str) -> str:
    for line in bill.get("lines", []):
        if line["label"] == label:
            return "Free" if line.get("struck") else "Yes"
        if label in ("Lunch", "Dinner") and line["label"] == "Meal bundle":
            return "Yes"
    return "No"


def _count(items: list, name: str) -> int:
    return sum(1 for item in items if item == name)


def confirmation_text(property_, request) -> str:
    details = request.details or {}
    bill = details.get("bill") or {}
    guests = bill.get("request") or {}
    first = (request.guest_name or "").strip().split(" ")[0] or "there"
    remaining = Decimal(request.total_amount) - Decimal(request.amount_due)
    status = "Paid in full" if remaining <= 0 else "Half paid"
    addons = guests.get("addons") or []
    lines = [
        HEADER.format(name=first),
        "The Booking Details is following:",
        f"Booking ID: {booking_ref(request)}",
        f"Stay Name: {property_.display_name or property_.name}",
        "Stay Selection: Full villa",
        f"Check in Date: {_day(request.check_in)}",
        f"Check Out Date: {_day(request.check_out)}",
        f"Number of Guests: {guests.get('adults', 0)} Adults, {guests.get('children', 0)} Children, {guests.get('pets', 0)} Pets",
        f"Breakfast Purchased: {_meal(bill, 'Breakfast')}",
        f"Lunch purchased: {_meal(bill, 'Lunch')}",
        f"Dinner purchased: {_meal(bill, 'Dinner')}",
        f"Grill kit: {_count(addons, 'bbq_experience')}",
        f"Food Platter: {guests.get('party_platter_qty', 0)}",
        f"Movie kit: {_count(addons, 'movie_kit')}",
        f"Floating kit: {_count(addons, 'floating_kit')}",
        f"Stay Purpose: {details.get('purpose', '')}",
        f"Payment Amount: {rupees(request.amount_due)}",
        f"Payment Remaining: {rupees(remaining)}",
        f"Payment Status: {status}",
        "Booking Status: Pending Host Approval",
    ]
    return "\n".join(lines) + "\n\n" + FOOTER


def stay_code_text(request) -> str:
    code = (request.details or {}).get("stay_code")
    return f"Your four-digit stay code is {code}. Quote it on any bank transfer." if code else ""


# -- the booking's required details ----------------------------------------------------------------

PURPOSES = ("Production House", "Wedding", "Birthday", "Celebration", "Couple", "Business", "Family / Friends", "Vacation", "Leisure", "Other")

# What a guest says -> the purpose the booking form accepts. First match wins; "Production House" first so a
# shoot is never filed as a celebration.
_PURPOSE_WORDS = (
    ("Production House", r"shoot|production|film|ad film|web series|photoshoot|crew"),
    ("Wedding", r"wedding|shaadi|sangeet|mehndi|engagement|reception"),
    ("Birthday", r"birthday|b'?day|bday"),
    ("Celebration", r"celebrat|anniversary|party|function|get[- ]?together|farewell|baby shower|housewarming"),
    ("Couple", r"couple|honeymoon|romantic|date"),
    ("Business", r"business|work|offsite|off-site|corporate|meeting|retreat|team"),
    ("Family / Friends", r"family|friends|reunion|group|buddies|cousins"),
    ("Vacation", r"vacation|holiday|trip|travel|tour"),
    ("Leisure", r"leisure|relax|weekend|chill|getaway|staycation|rest"),
)


def map_purpose(text: str | None) -> str | None:
    """The booking form's purpose for what the guest said, or None when nothing was said."""
    said = (text or "").strip()
    if not said:
        return None
    for exact in PURPOSES:
        if said.lower() == exact.lower():
            return exact
    for purpose, pattern in _PURPOSE_WORDS:
        if re.search(pattern, said, re.IGNORECASE):
            return purpose
    return "Other"


def is_shoot(purpose: str | None) -> bool:
    return purpose == "Production House"


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def valid_email(value: str | None) -> bool:
    return bool(value and _EMAIL_RE.match(value.strip()))


def valid_name(value: str | None) -> bool:
    """Letters (spaces, dots, apostrophes and hyphens allowed), at least 2 of them."""
    text = (value or "").strip()
    return len(re.findall(r"[^\W\d_]", text)) >= 2 and re.fullmatch(r"[^\W\d_][^\W\d_ .'\-]*(?:[ .'\-]+[^\W\d_]+)*[ .]*", text) is not None


def valid_mobile(digits: str | None) -> bool:
    return bool(digits) and 9 <= len(re.sub(r"\D", "", digits)) <= 15
