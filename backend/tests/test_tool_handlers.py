from datetime import date, timedelta

from sqlalchemy import select

from app.models.technician import Technician
from app.models.unanswered_question import UnansweredQuestion
from app.models.property import Property
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    NegotiateRateArgs,
    RecommendPropertiesArgs,
    SearchFaqArgs,
    SendWhatsappArgs,
    UpdateLeadArgs,
)
from app.services import lead_service, tool_handlers
from app.services.notification_service import list_notifications
from app.services.property.pitch_formatter import render_recommendation_text


async def test_check_calendar_available(test_property, db_session):
    today = date.today()
    args = CheckCalendarArgs(
        property_id=str(test_property.id), check_in=today + timedelta(days=10), check_out=today + timedelta(days=12)
    )
    result = await tool_handlers.handle_check_calendar(db_session, args)
    assert "AVAILABLE" in result
    assert test_property.name in result


async def test_check_calendar_unknown_property(db_session):
    args = CheckCalendarArgs(property_id="00000000-0000-0000-0000-000000000000", check_in=date.today(), check_out=date.today() + timedelta(days=1))
    result = await tool_handlers.handle_check_calendar(db_session, args)
    assert "couldn't find" in result.lower()


async def test_check_calendar_exceeds_max_guests(test_property, db_session):
    today = date.today()
    args = CheckCalendarArgs(
        property_id=str(test_property.id),
        check_in=today + timedelta(days=10),
        check_out=today + timedelta(days=12),
        num_guests=test_property.max_guests + 5,
    )
    result = await tool_handlers.handle_check_calendar(db_session, args)
    assert "sleeps up to" in result


async def test_get_pricing_includes_total(test_property, db_session):
    today = date.today()
    args = GetPricingArgs(
        property_id=str(test_property.id), check_in=today + timedelta(days=1), check_out=today + timedelta(days=3), num_guests=2
    )
    result = await tool_handlers.handle_get_pricing(db_session, args)
    assert "total" in result.lower()
    assert test_property.name in result


async def test_get_pricing_never_quotes_zero_when_base_price_is_zero(test_user, db_session):
    # Confirmed live 2026-07-23: a property with base_price=0 (and no live
    # price available) was quoted to a guest as "zero rupees for the night".
    # The guard must refuse to quote it and instead direct an escalation.
    zero_priced = Property(
        user_id=test_user.id,
        name="Zero Villa",
        city="Goa",
        exophone=f"+9180{__import__('uuid').uuid4().int % 10**8:08d}",
        base_price=0,
        max_guests=2,
    )
    db_session.add(zero_priced)
    await db_session.commit()
    await db_session.refresh(zero_priced)

    today = date.today()
    args = GetPricingArgs(
        property_id=str(zero_priced.id), check_in=today + timedelta(days=1), check_out=today + timedelta(days=2), num_guests=2
    )
    result = await tool_handlers.handle_get_pricing(db_session, args)
    # Returns the price-unavailable directive, not a numeric quote.
    assert result == tool_handlers._PRICE_UNAVAILABLE_MESSAGE
    assert "₹" not in result
    assert "escalate_to_host" in result


async def test_negotiate_rate_never_quotes_zero_when_base_price_is_zero(test_user, db_session):
    zero_priced = Property(
        user_id=test_user.id,
        name="Zero Villa",
        city="Goa",
        exophone=f"+9180{__import__('uuid').uuid4().int % 10**8:08d}",
        base_price=0,
        max_guests=2,
    )
    db_session.add(zero_priced)
    await db_session.commit()
    await db_session.refresh(zero_priced)

    today = date.today()
    args = NegotiateRateArgs(
        property_id=str(zero_priced.id), check_in=today + timedelta(days=1), check_out=today + timedelta(days=2), num_guests=2
    )
    result = await tool_handlers.handle_negotiate_rate(db_session, args, host_user_id=test_user.id)
    assert result == tool_handlers._PRICE_UNAVAILABLE_MESSAGE
    assert "₹" not in result
    assert "escalate_to_host" in result


async def test_get_pricing_backfills_lead_even_if_update_lead_never_called(test_property, test_call_session, db_session):
    # Regression: real calls were going through a full get_pricing/negotiate_rate
    # negotiation and ending with zero Lead row, because the model said its
    # escalation/booking phrases without reliably calling update_lead itself.
    # get_pricing/negotiate_rate/check_calendar now backfill a Lead as a side
    # effect, independent of the model ever calling update_lead.
    today = date.today()
    check_in, check_out = today + timedelta(days=1), today + timedelta(days=3)
    args = GetPricingArgs(
        property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2
    )
    await tool_handlers.handle_get_pricing(
        db_session, args, host_user_id=test_property.user_id, call_session_id=test_call_session.id
    )

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].properties_discussed == [test_property.name]
    assert leads[0].check_in == check_in
    assert leads[0].check_out == check_out
    assert leads[0].num_guests == 2


async def test_get_pricing_backfill_never_overwrites_guest_stated_dates(test_property, test_call_session, db_session):
    # update_lead's explicit dates (what the guest actually said) must win
    # over the backfill's blank-only semantics -- never silently overwritten
    # by a later get_pricing call for different dates the LLM is just checking.
    real_check_in, real_check_out = date.today() + timedelta(days=30), date.today() + timedelta(days=32)
    await lead_service.upsert_lead(
        db_session, test_property.user_id, test_call_session.id, check_in=real_check_in, check_out=real_check_out
    )

    other_check_in = date.today() + timedelta(days=5)
    args = GetPricingArgs(
        property_id=str(test_property.id),
        check_in=other_check_in,
        check_out=other_check_in + timedelta(days=1),
        num_guests=2,
    )
    await tool_handlers.handle_get_pricing(
        db_session, args, host_user_id=test_property.user_id, call_session_id=test_call_session.id
    )

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].check_in == real_check_in
    assert leads[0].check_out == real_check_out


async def test_get_pricing_without_call_session_id_never_creates_a_lead(test_property, db_session):
    # No call_session_id (e.g. a unit test, or a future non-voice caller) --
    # never create a lead with nothing to dedupe it against.
    today = date.today()
    args = GetPricingArgs(
        property_id=str(test_property.id), check_in=today + timedelta(days=1), check_out=today + timedelta(days=3), num_guests=2
    )
    await tool_handlers.handle_get_pricing(db_session, args, host_user_id=test_property.user_id, call_session_id=None)

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 0


async def test_dispatch_technician_finds_specialist(test_property, db_session):
    db_session.add(
        Technician(property_id=test_property.id, name="Plumber Joe", specialty="plumbing", phone="+911234567890", rating=4.9)
    )
    await db_session.commit()

    args = DispatchTechnicianArgs(property_id=str(test_property.id), issue_type="plumbing", urgency="high")
    result = await tool_handlers.handle_dispatch_technician(db_session, args, call_session_id=None)
    assert "Plumber Joe" in result

    notifications = await list_notifications(db_session)
    assert any("Plumber Joe" in n.message for n in notifications)


async def test_dispatch_technician_no_technician_on_file(test_property, db_session):
    args = DispatchTechnicianArgs(property_id=str(test_property.id), issue_type="lock", urgency="emergency")
    result = await tool_handlers.handle_dispatch_technician(db_session, args, call_session_id=None)
    assert "don't have" in result.lower()


async def test_send_whatsapp_creates_notification(test_property, db_session):
    args = SendWhatsappArgs(phone="+919999999999", message="Hello guest")
    result = await tool_handlers.handle_send_whatsapp(db_session, args, property_id=test_property.id, call_session_id=None)
    assert "queued" in result.lower()

    notifications = await list_notifications(db_session)
    assert any(n.channel == "whatsapp" and "Hello guest" in n.message for n in notifications)


async def test_escalate_to_host_creates_urgent_notification(test_property, db_session):
    args = EscalateToHostArgs(
        property_id=str(test_property.id),
        reason="No water",
        urgency="emergency",
        call_summary="Guest very upset",
        guest_phone="+919999999999",
    )
    result = await tool_handlers.handle_escalate_to_host(
        db_session, args, call_session_id=None, host_user_id=test_property.user_id
    )
    assert "emergency" in result.lower()

    notifications = await list_notifications(db_session)
    assert any(n.urgency == "emergency" for n in notifications)


async def test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty(test_property, db_session):
    # Regression: escalate_to_host used to only create a notification --
    # the rich call_summary/phone the model had already gathered never made
    # it into the Lead row, so a clearly hot lead stayed empty in the CRM.
    args = EscalateToHostArgs(
        property_id=str(test_property.id),
        reason="Finalize booking",
        urgency="medium",
        call_summary="Guest wants to book for 2 guests, July 3-5, total ₹12,987",
        guest_phone="+919999999999",
    )
    await tool_handlers.handle_escalate_to_host(
        db_session, args, call_session_id=None, host_user_id=test_property.user_id
    )

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].phone == "+919999999999"
    assert "12,987" in leads[0].conversation_summary
    assert leads[0].escalated is True


async def test_negotiate_rate_returns_message(test_property, db_session):
    today = date.today()
    args = NegotiateRateArgs(
        property_id=str(test_property.id),
        check_in=today + timedelta(days=1),
        check_out=today + timedelta(days=3),
        guest_offer=1,
        guest_loyalty="frequent",
    )
    result = await tool_handlers.handle_negotiate_rate(db_session, args)
    assert test_property.name in result


async def test_negotiate_rate_threads_host_user_id_to_disable_negotiation(test_property, test_user, db_session):
    """Confirms handle_negotiate_rate's new host_user_id parameter actually
    reaches pricing_engine.negotiate_rate -- a host with negotiation_allowed
    turned off should get the refusal message through this exact call path,
    the same one app/voice/tools.py's negotiate_rate wrapper uses."""
    test_user.negotiation_allowed = False
    await db_session.commit()

    today = date.today()
    args = NegotiateRateArgs(
        property_id=str(test_property.id),
        check_in=today + timedelta(days=1),
        check_out=today + timedelta(days=3),
        guest_offer=1,
        guest_loyalty="new",
    )
    result = await tool_handlers.handle_negotiate_rate(db_session, args, host_user_id=test_user.id)
    assert "best price" in result.lower()


async def test_search_faq_logs_gap_when_no_verified_answer(test_property, test_call_session, db_session):
    args = SearchFaqArgs(query="Do you allow pets?", property_id=str(test_property.id))
    result = await tool_handlers.handle_search_faq(
        db_session, args, test_property.user_id, test_property.id, test_call_session.id
    )
    assert "don't have verified information" in result

    gaps = (await db_session.scalars(select(UnansweredQuestion))).all()
    assert len(gaps) == 1
    assert gaps[0].question == "Do you allow pets?"
    assert gaps[0].normalized_question == "do you allow pets?"
    assert gaps[0].property_id == test_property.id
    assert gaps[0].call_session_id == test_call_session.id
    assert gaps[0].status == "pending"


async def test_search_faq_no_gap_logged_when_answer_found(test_property, db_session):
    # test_property's fixture already has a legacy Property.faq entry for
    # "Is wifi free?" -- a real match should not also create a gap row.
    args = SearchFaqArgs(query="Is wifi free?", property_id=str(test_property.id))
    result = await tool_handlers.handle_search_faq(db_session, args, test_property.user_id, test_property.id)
    assert "Yes" in result

    gaps = (await db_session.scalars(select(UnansweredQuestion))).all()
    assert len(gaps) == 0


async def test_update_lead_persists_occasion(test_property, test_call_session, db_session):
    args = UpdateLeadArgs(occasion="Guest said it's their honeymoon, wants a room with a view")
    await tool_handlers.handle_update_lead(db_session, args, test_property.user_id, test_call_session.id)

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].occasion == "Guest said it's their honeymoon, wants a room with a view"


async def test_update_lead_flags_incomplete_phone(test_property, test_call_session, db_session):
    # Regression: STT dropping a digit produced a 9-digit number that was
    # silently saved with no signal anywhere that it was wrong -- the guest
    # never got a WhatsApp message and no one found out until much later.
    args = UpdateLeadArgs(phone="932635908")
    result = await tool_handlers.handle_update_lead(db_session, args, test_property.user_id, test_call_session.id)
    assert "Saved." in result
    assert "9 digits, not 10" in result
    assert "repeat their full 10-digit number" in result


async def test_update_lead_no_warning_for_full_phone(test_property, test_call_session, db_session):
    args = UpdateLeadArgs(phone="9326359081")
    result = await tool_handlers.handle_update_lead(db_session, args, test_property.user_id, test_call_session.id)
    assert result == "Saved."


async def test_send_whatsapp_flags_incomplete_phone(test_property, db_session):
    args = SendWhatsappArgs(phone="932635908", message="Hello!")
    result = await tool_handlers.handle_send_whatsapp(db_session, args, test_property.id, None)
    assert "9 digits, not 10" in result


async def test_recommend_properties_matches_south_goa_locality_without_literal_text(test_user, db_session):
    # Regression: Azure's real city is "Colva" and its neighborhood_info
    # never contains the literal words "South Goa" -- an ILIKE match on
    # those exact words silently excluded it from a "South Goa" query even
    # though Colva unambiguously is South Goa.
    azure = Property(
        user_id=test_user.id,
        name="Azure 1bhk",
        city="Colva",
        exophone="+918011112222",
        base_price=3500,
        max_guests=2,
        neighborhood_info="2 min walk to Colva Beach.",
    )
    north_property = Property(
        user_id=test_user.id,
        name="Limón",
        city="Siolim",
        exophone="+918033334444",
        base_price=3000,
        max_guests=3,
        neighborhood_info="Centrally located in North Goa.",
    )
    db_session.add_all([azure, north_property])
    await db_session.commit()

    args = RecommendPropertiesArgs(preferred_location="South Goa")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    assert "Azure" in result_text
    assert "Limón" not in result_text


async def test_recommend_properties_south_goa_query_excludes_airport_travel_time_mention(test_user, db_session):
    """Regression for the exact live failure on 2026-07-27: Olive's real
    city is Siolim (North Goa), but its neighborhood_info mentions "Dabolim
    (South Goa airport)" purely as a travel-time reference point. Matching
    the literal "South Goa" phrase against neighborhood_info free text
    wrongly treated that incidental mention as the property being in South
    Goa -- a guest who explicitly asked for South Goa was recommended a
    North Goa property."""
    olive = Property(
        user_id=test_user.id,
        name="Olive-Wake up by the forest @ Pause Project 1bhk",
        city="Siolim",
        exophone="+918033336666",
        base_price=4500,
        max_guests=3,
        neighborhood_info=(
            "The Pause Project is centrally located in North Goa. Dabolim (South Goa airport) "
            "and Madgao railway station are both 75 mins away."
        ),
    )
    azure = Property(
        user_id=test_user.id,
        name="Azure 1bhk",
        city="Colva",
        exophone="+918011114444",
        base_price=3500,
        max_guests=2,
        neighborhood_info="2 min walk to Colva Beach.",
    )
    db_session.add_all([olive, azure])
    await db_session.commit()

    args = RecommendPropertiesArgs(preferred_location="South Goa")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    assert "Azure" in result_text
    assert "Olive" not in result_text


async def test_recommend_properties_matches_north_goa_locality(test_user, db_session):
    north_property = Property(
        user_id=test_user.id,
        name="Limón",
        city="Siolim",
        exophone="+918033335555",
        base_price=3000,
        max_guests=3,
        neighborhood_info="Centrally located in North Goa.",
    )
    south_property = Property(
        user_id=test_user.id,
        name="Azure 1bhk",
        city="Colva",
        exophone="+918011113333",
        base_price=3500,
        max_guests=2,
        neighborhood_info="2 min walk to Colva Beach.",
    )
    db_session.add_all([north_property, south_property])
    await db_session.commit()

    args = RecommendPropertiesArgs(preferred_location="North Goa")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    assert "Limón" in result_text
    assert "Azure" not in result_text


async def test_recommend_properties_suggests_combining_units_for_large_group(test_user, db_session):
    # No single property in the portfolio sleeps 6 -- rather than a flat
    # "couldn't find", the tool should surface the smaller units and let the
    # model suggest booking two of them together.
    unit_a = Property(
        user_id=test_user.id, name="Unit A", city="Siolim", exophone="+918011114444",
        base_price=3000, max_guests=3,
    )
    unit_b = Property(
        user_id=test_user.id, name="Unit B", city="Siolim", exophone="+918011115555",
        base_price=3200, max_guests=3,
    )
    db_session.add_all([unit_a, unit_b])
    await db_session.commit()

    args = RecommendPropertiesArgs(num_guests=6, preferred_location="Siolim")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    assert "Unit A" in result_text
    assert "Unit B" in result_text
    assert "book two of them together" in result_text


async def test_recommend_properties_combo_fallback_keeps_all_four_units(test_user, db_session):
    # Regression: the retrieval pipeline's final result was unconditionally
    # capped to 3 options even on the small-units-combo path, where
    # sql_search deliberately over-fetches to 4 (since the combo_note tells
    # the guest to consider pairing units, dropping one silently
    # contradicts that suggestion). All 4 units must survive to the final
    # rendered result.
    units = [
        Property(
            user_id=test_user.id, name=f"Unit {letter}", city="Siolim",
            exophone=f"+91801111{i}{i}{i}{i}", base_price=3000 + i * 100, max_guests=3,
        )
        for i, letter in enumerate("ABCD", start=1)
    ]
    db_session.add_all(units)
    await db_session.commit()

    args = RecommendPropertiesArgs(num_guests=6, preferred_location="Siolim")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    assert len(result.options) == 4


async def test_recommend_properties_filters_by_required_amenity(test_user, db_session):
    with_pool = Property(
        user_id=test_user.id, name="Nile", city="Siolim", exophone="+918011116666",
        base_price=4000, max_guests=3, amenities=["Private pool", "Wifi"],
        amenity_tags=["pool", "wifi"],
    )
    without_pool = Property(
        user_id=test_user.id, name="Mocha", city="Siolim", exophone="+918011117777",
        base_price=3800, max_guests=3, amenities=["Wifi"], amenity_tags=["wifi"],
    )
    db_session.add_all([with_pool, without_pool])
    await db_session.commit()

    args = RecommendPropertiesArgs(required_amenities=["swimming pool"])
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    assert "Nile" in result_text
    assert "Mocha" not in result_text


async def test_recommend_properties_near_landmark_boosts_matching_property(test_user, db_session):
    near = Property(
        user_id=test_user.id, name="Terra", city="Siolim", exophone="+918011118888",
        base_price=4500, max_guests=3,
        landmarks=[{"name": "Thalassa", "distance_minutes": 5, "mode": "walk"}],
    )
    far = Property(
        user_id=test_user.id, name="Whyt", city="Siolim", exophone="+918011119999",
        base_price=3000, max_guests=3,
    )
    db_session.add_all([near, far])
    await db_session.commit()

    args = RecommendPropertiesArgs(near_landmark="Thalassa")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    # Both still appear (soft signal, not a hard filter) but the matching
    # property is boosted ahead of the cheaper non-matching one.
    assert result_text.index("Terra") < result_text.index("Whyt")


async def test_recommend_properties_near_landmark_never_drops_all_results(test_user, db_session):
    # No property has structured landmark data yet -- must never zero out
    # results just because nothing matched a landmark query.
    only_property = Property(
        user_id=test_user.id, name="Pine", city="Siolim", exophone="+918011110000",
        base_price=4000, max_guests=3,
    )
    db_session.add(only_property)
    await db_session.commit()

    args = RecommendPropertiesArgs(near_landmark="Some Unknown Place")
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    result_text = render_recommendation_text(result)
    assert "Pine" in result_text
