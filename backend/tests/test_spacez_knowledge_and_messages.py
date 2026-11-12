"""SPACEZ ONLY. The villa profile and prices in the knowledge base, the bill summary that goes out with the
payment link (only the lines that apply), the prompt rules, and the tool that is offered only to Spacez hosts."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.property import Property
from app.models.user import User
from app.prompts.spacez_prompt import spacez_section
from app.prompts.system_prompt import build_lead_system_prompt, build_system_prompt
from app.services import spacez_booking_confirmation as bc
from app.services import spacez_pricing_terms, spacez_stay_bill as sb
from app.services.property.chunking import build_fact_chunks
from app.services.spacez_knowledge import facts_for
from app.voice.tools import build_voice_tools

ADDONS = {"bbq_experience": 1500, "movie_kit": 1000, "floating_kit": 800}


def _villa(name="Casa Blu", base=8000, weekend=None, **terms) -> Property:
    return Property(
        id=uuid.uuid4(), user_id=uuid.uuid4(), name=name, city="Bengaluru", base_price=base, max_guests=10, bedroom_count=4,
        weekend_rate_enabled=weekend is not None, weekend_price=weekend,
        pricing_terms={"base_guests": 8, "addons": ADDONS, "party_platter_price": 500, "area": "Whitefield, Bengaluru",
                       "parties": "small gatherings only", "pets": "allowed", "food": "Veg and non-veg", **terms},
    )


def _text(prop) -> str:
    return "\n".join(f.text for f in facts_for(prop))


# -- knowledge base ---------------------------------------------------------------------------------


def test_a_villas_profile_and_prices_are_knowledge_base_facts():
    text = _text(_villa(weekend=10000))

    assert "Whitefield, Bengaluru" in text and "4 bedrooms" in text
    assert "includes 8 guests and Casa Blu sleeps up to 10" in text and "₹1,000 per night" in text
    assert "Parties: small gatherings only." in text and "Pets: allowed." in text and "Food: Veg and non-veg." in text
    assert "₹8,000 per night on weeknights" in text and "Friday and Saturday nights are ₹10,000" in text
    assert "BBQ Experience ₹1,500 per night" in text and "Movie kit ₹1,000 per night" in text and "Floating kit ₹800 per night" in text
    assert "Party platter ₹500 per adult" in text
    assert "breakfast is ₹250 per adult per night" in text and "lunch is ₹400" in text and "₹100 per guest" in text
    assert "early check-in is a flat ₹1,000" in text.lower() or "Early check-in is a flat ₹1,000" in text


def test_a_villa_with_no_weekend_rate_says_every_night_costs_the_same():
    assert "₹8,000 per night, the same on every night" in _text(_villa())


def test_a_partner_villa_says_it_has_no_spacez_meals():
    text = _text(_villa(name="Soul Club", partner=True, food="Partner villa, no Spacez meals"))

    assert "partner villa" in text and "not offered" in text and "Food: Partner villa, no Spacez meals." in text
    assert "breakfast is" not in text


def test_pickup_and_bundle_prices_appear_only_when_on_file():
    assert "Pickup" not in _text(_villa()) and "Meal bundle" not in _text(_villa())
    text = _text(_villa(pickup_fare=2500, meal_bundle_price=600))
    assert "₹2,500, taxes included" in text and "₹600 per adult per night" in text


def test_nothing_in_the_knowledge_base_mentions_coupons_or_offers():
    assert not any(word in _text(_villa(weekend=9000)).lower() for word in ("coupon", "discount", "offer"))


def test_other_properties_get_no_spacez_facts_and_spacez_facts_join_the_fact_rows():
    plain = Property(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Plain", base_price=4000, max_guests=4)
    assert facts_for(plain) == []
    texts = [d.text for d in build_fact_chunks(_villa(weekend=10000), [])]
    assert any(t.startswith("Rates:") for t in texts) and any(t.startswith("Add-ons:") for t in texts)
    assert not any(t.startswith("Rates:") for t in (d.text for d in build_fact_chunks(plain, [])))


def test_the_weekend_rate_comes_only_from_the_property_toggle():
    assert spacez_pricing_terms.terms_for(_villa()).weekend_rate is None
    assert spacez_pricing_terms.terms_for(_villa(weekend=10000)).weekend_rate == Decimal(10000)
    off = _villa(weekend=10000)
    off.weekend_rate_enabled = False
    assert spacez_pricing_terms.terms_for(off).weekend_rate is None  # a price with the toggle off is ignored


# -- the bill summary that goes out with the payment link -----------------------------------------------------


def _request(bill, due, total, balance_on=None):
    return SimpleNamespace(
        details={"bill": bill}, guest_name="Asha Rao", check_in=date(2026, 12, 8), check_out=date(2026, 12, 10),
        amount_due=Decimal(due), total_amount=Decimal(total), balance_due_on=balance_on, short_url="https://pay.test/abc",
    )


def _bill(terms_kw=None, **req):
    terms = sb.Terms(base_rate=Decimal(7000), base_guests=8, max_guests=12, **(terms_kw or {}))
    request = sb.Request(date(2026, 12, 8), date(2026, 12, 10), adults=8, **req)
    return sb.to_dict(sb.compute(terms, request)) | {"request": {"adults": 8, "children": req.get("children", 0), "pets": 0}}


def test_the_message_has_a_one_line_calculation_the_grand_total_and_the_link():
    bill = _bill()
    message = bc.payment_request_text(SimpleNamespace(display_name="Deccan Escape", name="x"), _request(bill, 14700, 14700), 10)

    assert message.startswith("Hi Asha, complete your Spacez booking payment here: https://pay.test/abc")
    assert "Your booking: Deccan Escape, 8 Dec 2026 to 10 Dec 2026, 8 adult(s)" in message
    assert "Bill: Stay ₹14,000 (2 night(s) at ₹7,000) + GST (5%) ₹700" in message
    assert "Grand total: ₹14,700 (full payment)" in message
    for absent in ("Lunch", "Dinner", "Breakfast", "Pickup", "Extra guests", "Early check-in", "Late check-out", "Party platter", "Welcome", "service fee"):
        assert absent not in message, absent
    assert "\n" not in bc.calculation_line(bill)  # a template placeholder takes one line only


def test_every_applicable_line_is_in_the_calculation_with_its_amount():
    terms = sb.Terms(base_rate=Decimal(7000), base_guests=6, max_guests=12, pickup_fare=Decimal(2500), addons={"bbq_experience": Decimal(1500)})
    request = sb.Request(date(2026, 12, 8), date(2026, 12, 10), adults=8, lunch=True, welcome_drinks=True, pickup=True,
                         early_checkin=True, late_checkout=True, party_platter_qty=1, addons=("bbq_experience",))
    bill = sb.to_dict(sb.compute(terms, request)) | {"request": {"adults": 8, "children": 0, "pets": 0}}

    line = bc.calculation_line(bill)

    for part in ("Extra guests ₹4,000", "Lunch ₹6,400", "Welcome drinks & snacks ₹800", "Party platter ₹4,000", "Bbq Experience ₹3,000",
                 "Pickup & Drop ₹2,500", "Early check-in ₹1,000", "Late check-out ₹1,000"):
        assert part in line, part
    assert line.startswith("Stay ₹14,000") and " + GST (" in line


def test_a_split_booking_says_what_is_due_now_and_when_the_rest_is_due():
    bill = _bill()
    message = bc.payment_request_text(
        SimpleNamespace(display_name="V", name="v"), _request(bill, 7350, 14700, balance_on=date(2026, 12, 6)), 10
    )

    assert "Grand total: ₹7,350 now (split payment), ₹7,350 due by 6 Dec 2026" in message


def test_stay_codes_are_the_villas_letters_and_its_booking_number():
    assert bc.format_stay_code("Deccan Escape", 7) == "DECC-0007"
    assert bc.format_stay_code("Soul Club Villa", 42) == "SCV-0042"
    assert bc.format_stay_code("Sunset", 12345) == "SUNS-12345"
    assert bc.format_stay_code("Villa 21", 1) == "VILL-0001"
    assert bc.format_stay_code("1234", 3) == "STAY-0003"
    assert bc.format_stay_code("The Hill of Peace Resort", 9) == "HPR-0009"


# -- prompt + tool ---------------------------------------------------------------------------------------


def test_the_spacez_rules_are_in_the_prompt_only_for_a_host_with_a_spacez_villa():
    spacez, plain = _villa(), Property(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Plain", base_price=4000, max_guests=4)
    host = User(id=uuid.uuid4(), email="h@example.com", hashed_password="x", name="Asha")

    with_villa = build_lead_system_prompt(host, [spacez, plain])
    without = build_lead_system_prompt(host, [plain])
    single = build_system_prompt(spacez, None, host)

    for prompt in (with_villa, single):
        assert "SPACEZ BOOKINGS" in prompt and "request_early_late_checkout" in prompt
        assert "COUPONS, BANK OFFERS AND DISCOUNTS: never answer them" in prompt
        assert "payment link with them on WhatsApp shortly, with the bill summary" in " ".join(prompt.split())
    assert "SPACEZ" not in without and spacez_section([plain]) == "" and spacez_section([]) == ""


def _tool_names(**kw) -> set[str]:
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=uuid.uuid4(), **kw)
    return {getattr(t, "__name__", "") for t in tools}


def test_the_early_late_tool_is_offered_only_when_the_host_has_spacez_villas():
    assert "request_early_late_checkout" in _tool_names(spacez_enabled=True)
    assert "request_early_late_checkout" not in _tool_names()


def test_the_sample_data_file_is_valid_and_carries_the_documents_rates():
    import json
    from pathlib import Path

    document = json.loads((Path(__file__).parent.parent / "scripts" / "data" / "spacez_booking_terms.json").read_text())
    villas = {v["name"]: v for v in document["villas"]}

    assert {name: v["weekend_rate"] for name, v in villas.items()} == {
        "Casa Blu": 10000, "Crystal Hall": 25000, "Bombay Breeze": 8000, "Deccan Escape": 7000, "Soul Club": 13000
    }
    assert {name: (v["terms"]["base_guests"], v["max_guests"]) for name, v in villas.items()} == {
        "Casa Blu": (8, 10), "Crystal Hall": (10, 16), "Bombay Breeze": (6, 8), "Deccan Escape": (8, 12), "Soul Club": (10, 20)
    }
    assert villas["Soul Club"]["terms"]["partner"] is True
    for villa in villas.values():  # every entry validates, with the shared add-on prices merged in
        config = spacez_pricing_terms.PricingTermsConfig.model_validate({**document["common_terms"], **villa["terms"]})
        assert config.addons == ADDONS and config.party_platter_price == 500
