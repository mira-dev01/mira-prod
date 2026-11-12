"""SPACEZ ONLY. The WhatsApp messages of a Spacez booking, in the booking flow's format, and the stay code.
Pure functions over a PaymentRequest's details.

Each message is an approved WhatsApp template (app/services/whatsapp_templates.py says where the template's SID
goes and prints the text to create). The `*_values` functions give the values for its {{1}}, {{2}} ... in order;
the `*_text` functions fill the same template text, which is what goes out as free text while a template's SID is
not set yet -- so the guest reads the same message either way.

  * payment_link_values / payment_request_text    -- sent WITH the payment link once the guest has agreed to
    book: a one-or-two-line calculation of the bill and the grand total to pay.
  * confirmation_values / confirmation_text       -- sent once the payment succeeds (a stay).
  * shoot_held_values / shoot_held_text           -- sent once the payment succeeds for a shoot: held, not confirmed.
  * payment_unsuccessful_values / ..._text        -- the guest's attempt failed: the villa is held, try again.
  * stay code: `format_stay_code` (property letters + the property's booking number, e.g. DECC-0007).

Only for a request created from a full bill (details["bill"]); a plain payment link keeps the host's own
templates (billing_service.DEFAULT_TEMPLATES).
"""

import re
from decimal import Decimal

from app.services import whatsapp_templates
from app.services.spacez_stay_bill import rupees

_SKIP_WORDS = {"the", "a", "an", "of", "and", "by", "at", "in", "on", "to"}


def stay_code_prefix(property_name: str | None) -> str:
    """The letters a property's stay codes start with: its initials when the name has three or more words
    ("Soul Club Villa" -> SCV), otherwise the first four letters ("Deccan Escape" -> DECC, "Sunset" -> SUNS).
    Letters only, upper case; "STAY" for a name with no letters at all."""
    words = [w for w in re.findall(r"[A-Za-z]+", property_name or "") if w.lower() not in _SKIP_WORDS]
    if len(words) >= 3:
        return "".join(w[0] for w in words[:4]).upper()
    letters = "".join(words)
    return letters[:4].upper() or "STAY"


def format_stay_code(property_name: str | None, booking_number: int) -> str:
    """The guest's stay code: the property's letters, a dash and the property's running booking number
    (at least four digits): DECC-0007."""
    return f"{stay_code_prefix(property_name)}-{int(booking_number):04d}"


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


def _day_long(value) -> str:
    return value.strftime("%d %b %Y").lstrip("0")


def calculation_line(bill: dict) -> str:
    """The bill as ONE line the guest can check, every amount that makes up the grand total: the stay (with its
    working: nights x rate), then each extra, any discount taken off, the GST and any flat fee. Only lines that
    apply to this booking appear (the bill holds no others); a line that is waived is left out."""
    out = ""
    for line in bill.get("lines", []):
        label, amount, note = line["label"], line["amount"], line.get("note") or ""
        if line.get("struck") or label == "Total reservation amount":
            continue
        text = f"{label} {rupees(abs(amount))}" + (f" ({note})" if label == "Stay" and note else "")
        out += (f" - {text}" if amount < 0 else f" + {text}") if out else text
    return out


def _party(guests: dict) -> str:
    return f"{guests.get('adults', 0)} adult(s)" + (f", {guests['children']} child(ren)" if guests.get("children") else "") + (
        f", {guests['pets']} pet(s)" if guests.get("pets") else ""
    )


def _first(request) -> str:
    return (request.guest_name or "").strip().split(" ")[0] or "there"


def _pay_now(request) -> str:
    remaining = Decimal(request.total_amount) - Decimal(request.amount_due)
    if remaining > 0:
        later = f"due by {_day_long(request.balance_due_on)}" if request.balance_due_on else "due before check-in"
        return f"{rupees(request.amount_due)} now (split payment), {rupees(remaining)} {later}"
    return f"{rupees(request.amount_due)} (full payment)"


def payment_link_values(property_, request, hold_minutes: int = 0) -> list[str]:
    bill = (request.details or {}).get("bill") or {}
    guests = bill.get("request") or {}
    villa = property_.display_name or property_.name
    return [
        _first(request),
        request.short_url or "",
        f"{villa}, {_day_long(request.check_in)} to {_day_long(request.check_out)}, {_party(guests)}",
        calculation_line(bill),
        _pay_now(request),
    ]


def payment_request_text(property_, request, hold_minutes: int = 0) -> str:
    return whatsapp_templates.render("spacez_payment_link", payment_link_values(property_, request, hold_minutes))


def confirmation_values(property_, request) -> list[str]:
    details = request.details or {}
    bill = details.get("bill") or {}
    guests = bill.get("request") or {}
    remaining = Decimal(request.total_amount) - Decimal(request.amount_due)
    addons = guests.get("addons") or []
    return [
        _first(request),
        booking_ref(request),
        property_.display_name or property_.name,
        _day(request.check_in),
        _day(request.check_out),
        f"{guests.get('adults', 0)} Adults, {guests.get('children', 0)} Children, {guests.get('pets', 0)} Pets",
        _meal(bill, "Breakfast"),
        _meal(bill, "Lunch"),
        _meal(bill, "Dinner"),
        str(_count(addons, "bbq_experience")),
        str(guests.get("party_platter_qty", 0)),
        str(_count(addons, "movie_kit")),
        str(_count(addons, "floating_kit")),
        details.get("purpose", ""),
        rupees(request.amount_due),
        rupees(remaining),
        "Paid in full" if remaining <= 0 else "Half paid",
        details.get("stay_code") or "-",
    ]


def confirmation_text(property_, request) -> str:
    return whatsapp_templates.render("spacez_booking_confirmed", confirmation_values(property_, request))


def shoot_held_values(property_, request) -> list[str]:
    return [
        _first(request),
        booking_ref(request),
        property_.display_name or property_.name,
        _day(request.check_in),
        _day(request.check_out),
        (request.details or {}).get("stay_code") or "-",
    ]


def shoot_held_text(property_, request) -> str:
    return whatsapp_templates.render("spacez_shoot_held", shoot_held_values(property_, request))


def payment_unsuccessful_values(request, hold_until_text: str) -> list[str]:
    return [_first(request), request.short_url or "", hold_until_text]


def payment_unsuccessful_text(request, hold_until_text: str) -> str:
    return whatsapp_templates.render("spacez_payment_unsuccessful", payment_unsuccessful_values(request, hold_until_text))


def stay_code_text(request) -> str:
    code = (request.details or {}).get("stay_code")
    return f"Your stay code is {code}. Quote it on any bank transfer." if code else ""


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
