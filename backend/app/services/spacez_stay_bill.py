"""SPACEZ ONLY. The bill for a stay, line by line, exactly as the booking flow shows it (Spacez "Part 2: The bill").

Everything in this file is specific to the Spacez profile (villas with `properties.pricing_terms`); every
Spacez-only module is named `spacez_*` so they are easy to find. Nothing outside those modules should
need to know the rules below -- callers go through `compute()`.

Pure functions, no I/O, so the money is tested without a call. Everything the rules need but the
host's terms do not state is UNKNOWN, never defaulted: a meal bundle with no price, a pickup fare
nobody gave, an add-on kit the villa does not list. An unknown stops the quote with a plain reason
(BillUnknown) and the agent says the team will confirm; it never fills the gap with a number.

Rules implemented (all amounts in rupees):
  * nightly price x nights; Friday and Saturday nights use the villa's weekend rate when the host turned
    "weekend rate differs" on (properties.weekend_rate_enabled), otherwise the weeknight rate;
  * Rs 1,000 a night for each guest above the villa's base guests; pets are never counted, and
    children are free until the group goes over the base number, after which each costs like an adult;
  * breakfast / lunch / dinner per adult per night (breakfast free where the villa includes it);
  * welcome drinks & snacks per guest, party platter per adult, add-on kits per night;
  * a discount comes off the stay only, never off meals or add-ons;
  * GST on the total reservation amount: 5% when the total / nights is up to Rs 7,500, else 18%,
    rounded to the nearest rupee; the Spacez service fee is shown and charged at Rs 0;
  * pickup & drop sits outside GST and is always paid in full now;
  * early check-in / late check-out: a flat fee, a bill line ONLY when the guest asked for it; like pickup
    it sits outside GST and is paid in full now (the document does not say how these are taxed);
  * pay in full = total + GST + flat fees; split = half of (total + GST), rounded UP, + flat fees.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

GST_LOW, GST_HIGH = Decimal("0.05"), Decimal("0.18")
GST_THRESHOLD_PER_NIGHT = Decimal("7500")
EXTRA_GUEST_PER_NIGHT = Decimal("1000")

MAX_ADULTS_EVER = 20
MAX_CHILDREN = 5
MAX_PETS = 3

# Defaults the document gives for meal/welcome prices (a villa can override them in its terms).
DEFAULT_BREAKFAST = Decimal("250")
DEFAULT_LUNCH = Decimal("400")
DEFAULT_DINNER = Decimal("400")
DEFAULT_WELCOME = Decimal("100")
DEFAULT_PARTY_PLATTER = Decimal("500")


class BillUnknown(Exception):
    """Something the bill needs is not on file. `what` is safe to say aloud ("the Friday and Saturday rate")."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class BillInvalid(Exception):
    """The request breaks a booking limit. The message is safe to say aloud."""


@dataclass(frozen=True)
class Terms:
    """A villa's commercial terms. Anything left None is unknown, not zero."""

    base_rate: Decimal  # the weeknight rate, before GST
    base_guests: int
    max_guests: int
    weekend_rate: Decimal | None = None  # None = weekend nights use base_rate (the villa's rate does not differ)
    partner: bool = False  # a partner villa: no meal / welcome lines, no split payment
    breakfast_included: bool = False
    breakfast_price: Decimal = DEFAULT_BREAKFAST
    lunch_price: Decimal = DEFAULT_LUNCH
    dinner_price: Decimal = DEFAULT_DINNER
    welcome_price: Decimal = DEFAULT_WELCOME
    party_platter_price: Decimal = DEFAULT_PARTY_PLATTER
    # Per-night add-on kits the villa offers, name -> price. A kit not listed is not offered / not known.
    addons: dict[str, Decimal] = field(default_factory=dict)
    meal_bundle_price: Decimal | None = None  # per adult per night; None = not on file
    pickup_fare: Decimal | None = None  # None = not on file
    early_checkin_fee: Decimal = Decimal("1000")
    late_checkout_fee: Decimal = Decimal("1000")


@dataclass(frozen=True)
class Request:
    check_in: date
    check_out: date
    adults: int
    children: int = 0
    pets: int = 0
    breakfast: bool = False
    lunch: bool = False
    dinner: bool = False
    welcome_drinks: bool = False
    party_platter_qty: int = 0
    addons: tuple[str, ...] = ()  # kit names, one each
    pickup: bool = False
    early_checkin: bool = False  # the guest asked for it: only then does it appear in the bill
    late_checkout: bool = False
    discount: Decimal = Decimal("0")  # coupon / bank offer: off the stay only
    last_minute_offer: bool = False


@dataclass(frozen=True)
class Line:
    label: str
    amount: Decimal
    note: str = ""  # e.g. "8 adults x 2 nights x Rs 400"
    struck: bool = False  # shown struck through and charged at Rs 0


@dataclass(frozen=True)
class Bill:
    nights: int
    lines: tuple[Line, ...]
    stay_total: Decimal
    total_reservation: Decimal
    gst_rate: Decimal
    gst: Decimal
    pickup: Decimal
    full_payable: Decimal  # total + GST + pickup + early/late fees
    split_now: Decimal  # half of (total + GST), rounded up, + pickup + early/late fees
    split_later: Decimal  # the rest, due before check-in
    weekend_nights: int = 0
    extra_guests: int = 0
    early_checkin: Decimal = Decimal(0)
    late_checkout: Decimal = Decimal(0)
    weeknight_rate: Decimal = Decimal(0)  # what a weeknight costs (for "X on weeknights, Y on Fri/Sat")
    weekend_rate: Decimal = Decimal(0)  # what a Friday/Saturday night costs (== weeknight_rate when it does not differ)


def money(value) -> Decimal:
    """Whole rupees, half up."""
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def rupees(value) -> str:
    return f"₹{int(money(value)):,}"


def nights_of(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


def is_weekend_night(night: date) -> bool:
    return night.weekday() in (4, 5)  # Friday and Saturday


def validate(terms: Terms, request: Request) -> None:
    """The booking limits. Raises BillInvalid with a sentence the agent can say."""
    if request.check_out <= request.check_in:
        raise BillInvalid("Check-out must be after check-in.")
    if request.adults < 1:
        raise BillInvalid("A booking needs at least 1 adult (age 13 or over).")
    if request.adults > min(terms.max_guests, MAX_ADULTS_EVER):
        raise BillInvalid(
            f"This villa takes at most {min(terms.max_guests, MAX_ADULTS_EVER)} adults, and never more than {MAX_ADULTS_EVER}."
        )
    if request.children < 0 or request.children > MAX_CHILDREN:
        raise BillInvalid(f"Up to {MAX_CHILDREN} children (ages 2 to 12) can be added.")
    if request.pets < 0 or request.pets > MAX_PETS:
        raise BillInvalid(f"Up to {MAX_PETS} pets can be added.")
    if request.adults + request.children > terms.max_guests:
        raise BillInvalid(f"This villa sleeps at most {terms.max_guests} guests, children included.")


def extra_guest_heads(terms: Terms, request: Request) -> int:
    """Guests charged the extra-guest fee: everyone above the base number. Pets are never counted, and
    children only start to count once the group (adults + children) goes over the base number."""
    return max(0, request.adults + request.children - terms.base_guests)


def compute(terms: Terms, request: Request) -> Bill:
    validate(terms, request)
    night_dates = nights_of(request.check_in, request.check_out)
    nights = len(night_dates)
    lines: list[Line] = []

    # -- the stay ---------------------------------------------------------------------------
    # A villa whose rate does not differ at weekends (weekend_rate None) charges the weeknight rate on
    # Friday and Saturday nights as well -- never a guess, the host's own toggle decides.
    weekend_rate = terms.weekend_rate if terms.weekend_rate is not None else terms.base_rate
    weekend = [n for n in night_dates if is_weekend_night(n)]
    weeknights = nights - len(weekend)
    stay = terms.base_rate * weeknights + weekend_rate * len(weekend)
    if weekend and weekend_rate != terms.base_rate:
        parts = ([f"{weeknights} weeknight(s) at {rupees(terms.base_rate)}"] if weeknights else []) + [
            f"{len(weekend)} Friday/Saturday night(s) at {rupees(weekend_rate)}"
        ]
        note = " and ".join(parts)
    else:
        note = f"{nights} night(s) at {rupees(terms.base_rate)}"
    lines.append(Line("Stay", stay, note))

    heads = extra_guest_heads(terms, request)
    if heads:
        extra = EXTRA_GUEST_PER_NIGHT * heads * nights
        lines.append(Line("Extra guests", extra, f"{heads} guest(s) above {terms.base_guests} x {nights} night(s) x {rupees(EXTRA_GUEST_PER_NIGHT)}"))
        stay += extra
    stay_total = stay

    # -- food and extras (not shown at partner villas) -----------------------------------------
    extras = Decimal(0)
    wants_food = request.breakfast or request.lunch or request.dinner
    if terms.partner and (wants_food or request.welcome_drinks):
        raise BillInvalid("Partner villas do not offer Spacez meals or welcome drinks.")
    if not terms.partner:
        adult_nights = request.adults * nights
        if request.lunch and request.dinner:
            # Lunch and dinner together become the meal bundle (which also covers breakfast). Its price is
            # not in the terms unless the host gave one: never work it out from the single prices.
            if terms.meal_bundle_price is None:
                raise BillUnknown("the price of the meal bundle (lunch and dinner together)")
            lines.append(Line("Meal bundle", terms.meal_bundle_price * adult_nights, f"{request.adults} adult(s) x {nights} night(s)"))
            extras += terms.meal_bundle_price * adult_nights
        else:
            if request.breakfast:
                if terms.breakfast_included and not request.last_minute_offer:
                    lines.append(Line("Breakfast", Decimal(0), "included", struck=True))
                else:
                    cost = terms.breakfast_price * adult_nights
                    lines.append(Line("Breakfast", cost, f"{request.adults} adult(s) x {nights} night(s) x {rupees(terms.breakfast_price)}"))
                    extras += cost
            for chosen, label, price in ((request.lunch, "Lunch", terms.lunch_price), (request.dinner, "Dinner", terms.dinner_price)):
                if chosen:
                    cost = price * adult_nights
                    lines.append(Line(label, cost, f"{request.adults} adult(s) x {nights} night(s) x {rupees(price)}"))
                    extras += cost
        if request.welcome_drinks:
            guests = request.adults + request.children
            cost = terms.welcome_price * guests
            lines.append(Line("Welcome drinks & snacks", cost, f"{guests} guest(s) x {rupees(terms.welcome_price)}"))
            extras += cost

    if request.party_platter_qty:
        cost = terms.party_platter_price * request.adults * request.party_platter_qty
        lines.append(Line("Party platter", cost, f"{request.party_platter_qty} x {request.adults} adult(s) x {rupees(terms.party_platter_price)}"))
        extras += cost
    for kit in request.addons:
        price = terms.addons.get(kit)
        if price is None:
            raise BillUnknown(f"whether this villa offers the {kit.replace('_', ' ')} and its price")
        lines.append(Line(kit.replace("_", " ").title(), price * nights, f"{nights} night(s) x {rupees(price)}"))
        extras += price * nights

    # -- discount: off the stay only --------------------------------------------------------------
    discount = min(max(request.discount, Decimal(0)), stay_total)
    if discount:
        lines.append(Line("Discount", -discount, "off the stay only"))

    total_reservation = stay_total - discount + extras
    per_night = total_reservation / nights
    gst_rate = GST_LOW if per_night <= GST_THRESHOLD_PER_NIGHT else GST_HIGH
    gst = money(total_reservation * gst_rate)
    lines.append(Line("Total reservation amount", total_reservation))
    lines.append(Line(f"GST ({int(gst_rate * 100)}%)", gst))
    lines.append(Line("Spacez service fee (10%)", Decimal(0), "waived", struck=True))

    pickup = Decimal(0)
    if request.pickup:
        if terms.pickup_fare is None:
            raise BillUnknown("the fare for pickup and drop")
        pickup = terms.pickup_fare
        lines.append(Line("Pickup & Drop", pickup, "taxes included; paid in full now"))

    # Early check-in / late check-out are billed ONLY when the guest asked for them (flat fee, outside GST,
    # paid now). The team still has to confirm the villa is free, so the line says so.
    early = terms.early_checkin_fee if request.early_checkin else Decimal(0)
    late = terms.late_checkout_fee if request.late_checkout else Decimal(0)
    if early:
        lines.append(Line("Early check-in", early, "flat fee; subject to the team's confirmation"))
    if late:
        lines.append(Line("Late check-out", late, "flat fee; subject to the team's confirmation"))
    flat_fees = pickup + early + late

    base_with_gst = total_reservation + gst
    full = base_with_gst + flat_fees
    half = (base_with_gst / 2).quantize(Decimal("1"), rounding=ROUND_CEILING)
    split_now = half + flat_fees
    split_later = base_with_gst - half
    return Bill(
        nights=nights,
        lines=tuple(lines),
        stay_total=stay_total,
        total_reservation=total_reservation,
        gst_rate=gst_rate,
        gst=gst,
        pickup=pickup,
        full_payable=full,
        split_now=split_now,
        split_later=split_later,
        weekend_nights=len(weekend),
        extra_guests=heads,
        early_checkin=early,
        late_checkout=late,
        weeknight_rate=terms.base_rate,
        weekend_rate=weekend_rate,
    )


def to_dict(bill: Bill) -> dict:
    """A JSON-safe copy (kept with the quote and the payment request)."""
    return {
        "nights": bill.nights,
        "stay_total": int(bill.stay_total),
        "total_reservation": int(bill.total_reservation),
        "gst_rate": float(bill.gst_rate),
        "gst": int(bill.gst),
        "pickup": int(bill.pickup),
        "early_checkin": int(bill.early_checkin),
        "late_checkout": int(bill.late_checkout),
        "weeknight_rate": int(bill.weeknight_rate),
        "weekend_rate": int(bill.weekend_rate),
        "weekend_nights": bill.weekend_nights,
        "full_payable": int(bill.full_payable),
        "split_now": int(bill.split_now),
        "split_later": int(bill.split_later),
        "lines": [{"label": ln.label, "amount": int(ln.amount), "note": ln.note, "struck": ln.struck} for ln in bill.lines],
    }
