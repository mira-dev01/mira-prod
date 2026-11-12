"""The bill (app/services/spacez_stay_bill.py) against the booking-flow rules, including the document's own
two worked examples."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.services import spacez_stay_bill as sb
from app.services.spacez_stay_bill import BillInvalid, BillUnknown, Request, Terms


def terms(**kw) -> Terms:
    base = dict(base_rate=D(10000), base_guests=8, max_guests=10)
    base.update(kw)
    return Terms(**base)


def req(check_in="2026-12-08", check_out="2026-12-10", **kw) -> Request:
    base = dict(check_in=date.fromisoformat(check_in), check_out=date.fromisoformat(check_out), adults=8)
    base.update(kw)
    return Request(**base)


# -- the document's worked examples --------------------------------------------------------------


def test_casa_blu_example_eight_adults_two_nights_with_lunch():
    bill = sb.compute(terms(), req(lunch=True))  # Tue 8 Dec, Wed 9 Dec: weeknights

    assert bill.stay_total == 20000
    assert [ln.amount for ln in bill.lines if ln.label == "Lunch"] == [6400]
    assert bill.total_reservation == 26400  # Rs 13,200 a night, so
    assert bill.gst_rate == D("0.18") and bill.gst == 4752
    assert bill.full_payable == 31152
    assert (bill.split_now, bill.split_later) == (15576, 15576)


def test_first_test_deccan_escape_two_weeknights_for_a_birthday():
    bill = sb.compute(terms(base_rate=D(7000), max_guests=12), req())

    assert bill.total_reservation == 14000  # Rs 7,000 a night, so
    assert bill.gst_rate == D("0.05") and bill.gst == 700
    assert bill.full_payable == 14700
    assert bill.split_now == 7350


# -- GST ------------------------------------------------------------------------------------------


def test_gst_is_5_percent_up_to_7500_a_night_inclusive_and_18_above():
    at = sb.compute(terms(base_rate=D(7500)), req())
    above = sb.compute(terms(base_rate=D(7501)), req())
    assert at.gst_rate == D("0.05") and above.gst_rate == D("0.18")


def test_the_gst_rate_looks_at_the_total_per_night_so_extras_can_tip_it():
    # 7,000 a night alone is 5%; with lunch (6,400 over 2 nights) the total is 20,400 = 10,200 a night: 18%.
    assert sb.compute(terms(base_rate=D(7000)), req()).gst_rate == D("0.05")
    assert sb.compute(terms(base_rate=D(7000)), req(lunch=True)).gst_rate == D("0.18")


def test_gst_rounds_to_the_nearest_rupee_half_up():
    # 5% of 10,010 = 500.5 -> 501 (half up, not banker's rounding)
    bill = sb.compute(terms(base_rate=D(5005)), req())
    assert bill.total_reservation == 10010 and bill.gst == 501


def test_split_now_rounds_up():
    # (total + GST) = 14,700 + 1 -> odd, so half rounds up: now > later
    bill = sb.compute(terms(base_rate=D("5000.5")), req())
    assert bill.split_now >= bill.split_later and bill.split_now + bill.split_later == bill.total_reservation + bill.gst


# -- the stay -------------------------------------------------------------------------------------


def test_friday_and_saturday_nights_use_the_weeknight_rate_when_the_villa_has_no_weekend_rate():
    bill = sb.compute(terms(), req("2026-12-11", "2026-12-13"))  # Fri 11, Sat 12
    assert bill.stay_total == 20000 and bill.weekend_nights == 2  # 2 nights at the 10,000 rate
    assert bill.weekend_rate == bill.weeknight_rate == 10000


def test_a_configured_weekend_rate_is_used_for_those_nights_only():
    # Thu 10 (weeknight), Fri 11, Sat 12 (weekend), Sun 13 checkout
    bill = sb.compute(terms(weekend_rate=D(14000)), req("2026-12-10", "2026-12-13"))
    assert bill.stay_total == 10000 + 14000 * 2 and bill.weekend_nights == 2


def test_a_stay_that_ends_on_friday_morning_has_no_weekend_night():
    # nights of Wed 9 and Thu 10; check-out Fri 11
    assert sb.compute(terms(), req("2026-12-09", "2026-12-11")).weekend_nights == 0


def test_extra_guests_pay_1000_a_night_each_above_base():
    bill = sb.compute(terms(), req(adults=10))  # 2 above base 8, 2 nights
    assert [ln.amount for ln in bill.lines if ln.label == "Extra guests"] == [4000]
    assert bill.extra_guests == 2


def test_children_are_free_until_the_group_goes_over_base_then_count_like_adults():
    assert sb.compute(terms(), req(adults=6, children=2)).extra_guests == 0  # 8 in total: free
    assert sb.compute(terms(), req(adults=6, children=3)).extra_guests == 1  # 9: one child above base pays
    assert sb.compute(terms(), req(adults=9, children=1)).extra_guests == 2  # 10 in total


def test_pets_are_never_charged():
    assert sb.compute(terms(), req(pets=3)).stay_total == 20000


# -- food and extras --------------------------------------------------------------------------------


def test_breakfast_is_free_where_included_but_paid_with_a_last_minute_offer():
    free = sb.compute(terms(breakfast_included=True), req(breakfast=True))
    assert [(ln.amount, ln.struck) for ln in free.lines if ln.label == "Breakfast"] == [(0, True)]
    paid = sb.compute(terms(breakfast_included=True), req(breakfast=True, last_minute_offer=True))
    assert [ln.amount for ln in paid.lines if ln.label == "Breakfast"] == [250 * 8 * 2]


def test_breakfast_lunch_dinner_are_per_adult_per_night():
    bill = sb.compute(terms(), req(adults=4, breakfast=True, lunch=True))
    assert {ln.label: ln.amount for ln in bill.lines if ln.label in ("Breakfast", "Lunch")} == {"Breakfast": 2000, "Lunch": 3200}


def test_lunch_plus_dinner_needs_a_bundle_price_and_never_adds_the_two_singles():
    with pytest.raises(BillUnknown) as unknown:
        sb.compute(terms(), req(lunch=True, dinner=True))
    assert "meal bundle" in unknown.value.what
    bill = sb.compute(terms(meal_bundle_price=D(700)), req(adults=4, lunch=True, dinner=True, breakfast=True))
    meal_lines = [ln for ln in bill.lines if "meal" in ln.label.lower() or ln.label in ("Lunch", "Dinner", "Breakfast")]
    assert [(ln.label, ln.amount) for ln in meal_lines] == [("Meal bundle", 700 * 4 * 2)]  # covers breakfast too


def test_welcome_drinks_are_per_guest_children_included_and_once():
    bill = sb.compute(terms(), req(adults=6, children=2, welcome_drinks=True))
    assert [ln.amount for ln in bill.lines if ln.label.startswith("Welcome")] == [800]


def test_partner_villas_have_no_meal_or_welcome_lines():
    with pytest.raises(BillInvalid):
        sb.compute(terms(partner=True), req(lunch=True))
    with pytest.raises(BillInvalid):
        sb.compute(terms(partner=True), req(welcome_drinks=True))


def test_party_platter_is_per_adult():
    bill = sb.compute(terms(), req(adults=4, party_platter_qty=2))
    assert [ln.amount for ln in bill.lines if ln.label == "Party platter"] == [500 * 4 * 2]


def test_an_addon_kit_is_per_night_and_only_where_the_villa_offers_it():
    bill = sb.compute(terms(addons={"bbq_experience": D(1500)}), req(addons=("bbq_experience",)))
    assert [ln.amount for ln in bill.lines if ln.label == "Bbq Experience"] == [3000]
    with pytest.raises(BillUnknown):
        sb.compute(terms(), req(addons=("bbq_experience",)))


def test_a_discount_comes_off_the_stay_only_never_meals():
    bill = sb.compute(terms(), req(lunch=True, discount=D(30000)))  # more than the stay itself
    assert bill.total_reservation == 6400  # the stay is wiped out, the lunch is not touched
    assert [ln.amount for ln in bill.lines if ln.label == "Lunch"] == [6400]


# -- pickup -----------------------------------------------------------------------------------------


def test_early_checkin_and_late_checkout_are_lines_only_when_asked_flat_outside_gst_and_paid_now():
    plain = sb.compute(terms(), req())
    asked = sb.compute(terms(), req(early_checkin=True, late_checkout=True))
    labels = [ln.label for ln in asked.lines]
    assert "Early check-in" not in [ln.label for ln in plain.lines] and "Late check-out" not in [ln.label for ln in plain.lines]
    assert "Early check-in" in labels and "Late check-out" in labels
    assert asked.gst == plain.gst and asked.total_reservation == plain.total_reservation  # outside GST
    assert asked.full_payable == plain.full_payable + 2000
    assert asked.split_now == plain.split_now + 2000 and asked.split_later == plain.split_later


def test_pickup_is_outside_gst_and_paid_in_full_even_on_split():
    bill = sb.compute(terms(base_rate=D(7000), pickup_fare=D(1800)), req(pickup=True))
    assert bill.gst == 700 and bill.full_payable == 14000 + 700 + 1800
    assert bill.split_now == 7350 + 1800 and bill.split_later == 7350


def test_pickup_without_a_fare_on_file_is_unknown_not_free():
    with pytest.raises(BillUnknown) as unknown:
        sb.compute(terms(), req(pickup=True))
    assert "pickup" in unknown.value.what


# -- limits -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw, expect",
    [
        (dict(adults=0), "at least 1 adult"),
        (dict(adults=11), "at most 10 adults"),
        (dict(adults=8, children=6), "Up to 5 children"),
        (dict(pets=4), "Up to 3 pets"),
        (dict(adults=8, children=3), "sleeps at most 10"),
    ],
)
def test_booking_limits_are_enforced(kw, expect):
    with pytest.raises(BillInvalid, match=expect):
        sb.compute(terms(), req(**kw))


def test_adults_never_exceed_20_even_for_a_bigger_villa():
    with pytest.raises(BillInvalid, match="never more than 20"):
        sb.compute(terms(max_guests=30), req(adults=21))


def test_checkout_must_be_after_checkin():
    with pytest.raises(BillInvalid):
        sb.compute(terms(), req("2026-12-10", "2026-12-10"))


def test_the_bill_round_trips_to_json():
    out = sb.to_dict(sb.compute(terms(base_rate=D(7000)), req()))
    assert out["full_payable"] == 14700 and out["split_now"] == 7350 and out["lines"][0]["label"] == "Stay"


def test_the_stay_line_names_only_the_nights_it_charges():
    mixed = sb.compute(terms(weekend_rate=D(14000)), req("2026-12-10", "2026-12-13"))  # Thu, Fri, Sat
    weekend_only = sb.compute(terms(weekend_rate=D(14000)), req("2026-12-11", "2026-12-13"))
    same_rate = sb.compute(terms(), req("2026-12-11", "2026-12-13"))
    note = lambda bill: next(ln.note for ln in bill.lines if ln.label == "Stay")  # noqa: E731

    assert note(mixed) == "1 weeknight(s) at ₹10,000 and 2 Friday/Saturday night(s) at ₹14,000"
    assert note(weekend_only) == "2 Friday/Saturday night(s) at ₹14,000"
    assert note(same_rate) == "2 night(s) at ₹10,000"
