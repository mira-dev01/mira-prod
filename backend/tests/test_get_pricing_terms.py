"""get_pricing for a villa with full booking terms: the bill, GST and both ways to pay; unknowns
that stop the quote; no discounts."""

from datetime import date, timedelta

from app.models.property import Property
from app.schemas.tool import GetPricingArgs, NegotiateRateArgs
from app.services import tool_handlers
from app.services.pricing_terms import terms_for
from app.voice.conversation_state import ConversationState


def _next_weekday(weekday: int, after_days: int = 21) -> date:
    d = date.today() + timedelta(days=after_days)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


async def _terms_property(db, user, **terms):
    prop = Property(
        user_id=user.id, name="Deccan Escape", city="Hyderabad", base_price=7000, max_guests=12,
        exophone="+918000000001", pricing_terms={"base_guests": 8, **terms},
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


def _args(prop, check_in, nights=2, guests=8, **kw):
    return GetPricingArgs(
        property_id=str(prop.id), check_in=check_in, check_out=check_in + timedelta(days=nights), num_guests=guests, **kw
    )


async def test_the_documents_first_test_quote(db_session, test_user):
    prop = await _terms_property(db_session, test_user)
    tue = _next_weekday(1)  # Tue + Wed nights: weeknights
    seen = []

    result = await tool_handlers.handle_get_pricing(
        db_session, _args(prop, tue), test_user.id, on_priced=lambda p, b: seen.append(b)
    )

    assert "₹7,000 a night" in result and "₹14,000 for 2 nights" in result
    assert "GST at 5% is ₹700" in result and "₹14,700 in all" in result
    assert "split: ₹7,350 now and ₹7,350 before check-in" in result
    assert seen[0].total == 14700 and seen[0].bill["split_now"] == 7350  # the one number the guard checks


async def test_the_bill_rides_with_the_recorded_quote(db_session, test_user):
    prop = await _terms_property(db_session, test_user)
    state = ConversationState()
    tue = _next_weekday(1)

    await tool_handlers.handle_get_pricing(
        db_session, _args(prop, tue, children=2, meals=["lunch"]), test_user.id,
        on_priced=lambda p, b: state.record_quoted_price(p.name, tue.isoformat(), (tue + timedelta(days=2)).isoformat(), b.total, nights=b.nights, bill=b.bill),
    )

    bill = state.quoted_price["bill"]
    assert bill["request"] == {"adults": 6, "children": 2, "pets": 0, "meals": ["lunch"], "welcome_drinks": False,
                               "party_platter_qty": 0, "addons": [], "pickup": False}
    assert bill["total_reservation"] == 14000 + 6 * 400 * 2


async def test_a_weekend_night_without_a_weekend_rate_stops_the_quote(db_session, test_user):
    prop = await _terms_property(db_session, test_user)
    fri = _next_weekday(4)

    result = await tool_handlers.handle_get_pricing(db_session, _args(prop, fri), test_user.id)

    assert "Friday and Saturday rate is not on file" in result and "Do not guess a number" in result and "escalate_to_host" in result


async def test_with_a_weekend_rate_the_friday_stay_is_quoted(db_session, test_user):
    prop = await _terms_property(db_session, test_user, weekend_rate=9000)
    fri = _next_weekday(4)

    result = await tool_handlers.handle_get_pricing(db_session, _args(prop, fri), test_user.id)

    assert "₹18,000 for 2 nights" in result  # Fri + Sat at the weekend rate


async def test_a_partner_villa_has_no_split_and_no_meals(db_session, test_user):
    prop = await _terms_property(db_session, test_user, partner=True)
    tue = _next_weekday(1)

    quote = await tool_handlers.handle_get_pricing(db_session, _args(prop, tue), test_user.id)
    meals = await tool_handlers.handle_get_pricing(db_session, _args(prop, tue, meals=["lunch"]), test_user.id)

    assert "partner villa" in quote and "no split payment" in quote and "split:" not in quote
    assert "Partner villas do not offer Spacez meals" in meals


async def test_booking_limits_and_unknown_extras_are_reported_plainly(db_session, test_user):
    prop = await _terms_property(db_session, test_user)
    tue = _next_weekday(1)

    too_many = await tool_handlers.handle_get_pricing(db_session, _args(prop, tue, guests=13), test_user.id)
    bbq = await tool_handlers.handle_get_pricing(db_session, _args(prop, tue, addons=["bbq experience"]), test_user.id)
    bundle = await tool_handlers.handle_get_pricing(db_session, _args(prop, tue, meals=["lunch", "dinner"]), test_user.id)
    bad_meal = await tool_handlers.handle_get_pricing(db_session, _args(prop, tue, meals=["supper"]), test_user.id)

    assert "at most" in too_many
    assert "bbq experience" in bbq and "not on file" in bbq
    assert "meal bundle" in bundle and "not on file" in bundle
    assert "breakfast, lunch or dinner" in bad_meal


async def test_exact_dates_are_required_for_such_a_villa(db_session, test_user):
    prop = await _terms_property(db_session, test_user)

    result = await tool_handlers.handle_get_pricing(
        db_session, GetPricingArgs(property_id=str(prop.id), nights=2, num_guests=8), test_user.id
    )

    assert "exact dates" in result


async def test_there_is_no_discount_to_give(db_session, test_user):
    prop = await _terms_property(db_session, test_user)
    tue = _next_weekday(1)

    result = await tool_handlers.handle_negotiate_rate(
        db_session,
        NegotiateRateArgs(property_id=str(prop.id), check_in=tue, check_out=tue + timedelta(days=2), num_guests=8),
        test_user.id,
    )

    assert "no discount available" in result and "do not offer or hint" in result


async def test_a_property_without_terms_keeps_the_flat_quote(db_session, test_property, test_user):
    assert terms_for(test_property) is None
    tue = _next_weekday(1)

    result = await tool_handlers.handle_get_pricing(
        db_session, GetPricingArgs(property_id=str(test_property.id), check_in=tue, check_out=tue + timedelta(days=2), num_guests=2), test_user.id
    )

    assert "₹4,000 per night" in result and "GST" not in result


async def test_broken_terms_fall_back_to_no_terms_not_a_crash(db_session, test_user):
    prop = await _terms_property(db_session, test_user)
    prop.pricing_terms = {"base_guests": "not a number"}
    await db_session.commit()

    assert terms_for(prop) is None
