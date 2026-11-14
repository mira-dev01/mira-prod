from datetime import date, timedelta

from app.models.booking import Booking
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.schemas.tool import RecommendPropertiesArgs
from app.services import embedding_service
from app.services.property.pitch_formatter import render_recommendation_text
from app.services.property.retrieval import orchestrator


async def test_orchestrator_returns_sql_only_when_purpose_of_stay_absent(test_user, db_session):
    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129001")
    db_session.add(property_)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert len(result.options) == 1
    assert result.options[0].spoken_name == "Pine"


async def test_orchestrator_never_returns_more_than_three_options(test_user, db_session):
    properties = [
        Property(user_id=test_user.id, name=f"Unit{i}", base_price=1000 * i, max_guests=2, exophone=f"+9180{i:08d}")
        for i in range(1, 6)
    ]
    db_session.add_all(properties)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert len(result.options) <= 3


async def test_orchestrator_semantic_search_never_fires_for_pure_structured_query(test_user, db_session, monkeypatch):
    # No purpose_of_stay -- semantic search must not be invoked at all, even
    # if get_embedding would otherwise succeed. Patch get_embedding to raise
    # so this test fails loudly if semantic search is ever wrongly invoked.
    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129002")
    db_session.add(property_)
    await db_session.commit()

    async def _should_not_be_called(text):
        raise AssertionError("get_embedding should never be called for a purely structured query")

    monkeypatch.setattr(embedding_service, "get_embedding", _should_not_be_called)

    args = RecommendPropertiesArgs(required_amenities=None, budget_amount=5000, budget_basis="per_night")
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert len(result.options) == 1


async def test_orchestrator_semantic_search_enriches_when_sql_underreturns(test_user, db_session, monkeypatch):
    # SQL returns zero results (budget too low for anything), but a
    # subjective purpose_of_stay is present -- semantic search should widen
    # to the host's full portfolio and find a match via chunk embeddings.
    property_ = Property(user_id=test_user.id, name="Cozy Cabin", base_price=9000, max_guests=2, exophone="+918011129003")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    chunk = PropertyChunk(
        property_id=property_.id, chunk_type="overview", text="A romantic cozy cabin getaway.",
        embedding=[1.0, 0.0, 0.0],
    )
    db_session.add(chunk)
    await db_session.commit()

    async def _fake_get_embedding(text):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(embedding_service, "get_embedding", _fake_get_embedding)

    args = RecommendPropertiesArgs(purpose_of_stay="romantic getaway")
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert any(card.spoken_name == "Cozy Cabin" for card in result.options)


async def test_handle_recommend_properties_delegates_to_orchestrator(test_user, db_session):
    from app.services import tool_handlers

    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129004")
    db_session.add(property_)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    assert result.options[0].spoken_name == "Pine"


async def test_recommend_properties_excludes_booked_property_when_dates_known(test_user, db_session):
    """Phase 2.4 (documentation/agent-conversation-improvement.md): two
    properties matching every other criterion, one with a conflicting
    Booking row for the guest's stated dates -- confirm only the available
    one is returned when dates are passed through."""
    booked = Property(
        user_id=test_user.id, name="Booked Villa", base_price=4000, max_guests=4, exophone="+918011129005"
    )
    available = Property(
        user_id=test_user.id, name="Open Villa", base_price=4200, max_guests=4, exophone="+918011129006"
    )
    db_session.add_all([booked, available])
    await db_session.commit()
    await db_session.refresh(booked)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=2)
    db_session.add(
        Booking(property_id=booked.id, check_in=check_in, check_out=check_out, status="confirmed")
    )
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out
    )

    names = [card.spoken_name for card in result.options]
    assert "Open Villa" in names
    assert "Booked Villa" not in names


async def test_recommend_properties_byte_identical_when_dates_not_known(test_user, db_session):
    """Confirms this is purely additive -- behavior when dates aren't yet
    known (the common case, per Phase 2.3's earlier-recommend goal) must be
    unchanged from before this task."""
    booked = Property(
        user_id=test_user.id, name="Booked Villa", base_price=4000, max_guests=4, exophone="+918011129007"
    )
    db_session.add(booked)
    await db_session.commit()
    await db_session.refresh(booked)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=2)
    db_session.add(
        Booking(property_id=booked.id, check_in=check_in, check_out=check_out, status="confirmed")
    )
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)

    # No dates passed -- today's unfiltered behavior, the booked property
    # still shows up (check_calendar catches the real conflict downstream).
    assert any(card.spoken_name == "Booked Villa" for card in result.options)


async def test_recommend_properties_confidence_strong_for_one_clean_match(test_user, db_session):
    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129301")
    db_session.add(property_)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.recommendation_confidence == "strong"


async def test_recommend_properties_confidence_weak_for_combo_fallback(test_user, db_session):
    unit_a = Property(user_id=test_user.id, name="Unit A", base_price=3000, max_guests=3, exophone="+918011129302")
    unit_b = Property(user_id=test_user.id, name="Unit B", base_price=3200, max_guests=3, exophone="+918011129303")
    db_session.add_all([unit_a, unit_b])
    await db_session.commit()

    args = RecommendPropertiesArgs(num_guests=6)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.recommendation_confidence == "weak"


async def test_recommend_properties_diversifies_leader_across_calls_with_comparable_options(test_user, db_session):
    """Phase 2.5 (documentation/agent-conversation-improvement.md), wired
    end-to-end through orchestrator.recommend_properties: two comparably
    priced properties, many distinct call_session_ids -- confirm real
    variety in which one leads, not always the cheapest."""
    import uuid as uuid_module

    a = Property(user_id=test_user.id, name="A", base_price=5000, max_guests=2, exophone="+918011129201")
    b = Property(user_id=test_user.id, name="B", base_price=5100, max_guests=2, exophone="+918011129202")
    db_session.add_all([a, b])
    await db_session.commit()

    leaders = set()
    for _ in range(20):
        args = RecommendPropertiesArgs()
        result = await orchestrator.recommend_properties(
            db_session, args, test_user.id, call_session_id=uuid_module.uuid4()
        )
        leaders.add(result.options[0].spoken_name)

    assert len(leaders) > 1


async def test_recommend_properties_no_call_session_id_uses_stable_default_order(test_user, db_session):
    """Confirms this is purely additive when call_session_id isn't passed --
    behavior byte-identical to before this task."""
    a = Property(user_id=test_user.id, name="A", base_price=5000, max_guests=2, exophone="+918011129203")
    b = Property(user_id=test_user.id, name="B", base_price=5100, max_guests=2, exophone="+918011129204")
    db_session.add_all([a, b])
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.options[0].spoken_name == "A"


async def test_more_premium_than_shown_boost_survives_diversify_rotation(test_user, db_session):
    """Recommendation conversations ("Phase X") self-review fix:
    diversify_leading_candidates rotates purely by price within a
    comparable-price band, with no awareness of apply_premium_boost's own
    ordering -- without orchestrator.py explicitly skipping diversification
    when more_premium_than_shown is set, a premium property that
    sql_search.py already boosted to the front could get silently rotated
    back behind a cheaper, non-premium option that happens to fall in the
    same price band, undoing the exact thing the guest asked for. "session-a"
    is a call_session_id confirmed (via the same rotation-seed hash
    diversify_leading_candidates uses) to rotate a 2-item comparable band --
    if the fix regresses, this test starts flaking/failing on that seed."""
    regular = Property(
        user_id=test_user.id, name="Regular", base_price=5000, max_guests=2,
        exophone="+918011129401", is_premium=False,
    )
    premium = Property(
        user_id=test_user.id, name="Premium", base_price=5100, max_guests=2,
        exophone="+918011129402", is_premium=True,
    )
    db_session.add_all([regular, premium])
    await db_session.commit()

    args = RecommendPropertiesArgs(more_premium_than_shown=True)
    # No call_session_id is the simplest case.
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.options[0].spoken_name == "Premium"

    # This specific call_session_id is confirmed (via the same rotation-seed
    # hash diversify_leading_candidates uses) to rotate a 2-item comparable
    # band -- with the fix, diversification is skipped entirely whenever
    # more_premium_than_shown is set, so Premium stays first regardless of
    # which call_session_id is passed.
    import uuid as uuid_module

    result_with_session = await orchestrator.recommend_properties(
        db_session, args, test_user.id, call_session_id=uuid_module.UUID(int=1)
    )
    assert result_with_session.options[0].spoken_name == "Premium"


async def test_recommend_properties_availability_check_fails_open_on_error(test_user, db_session, monkeypatch):
    """Must fail open -- an availability pre-check erroring/timing out is
    never a reason a recommendation should be blocked entirely.
    Availability-first recommendations, Implementation 3: orchestrator.py
    now calls partial_availability_for_candidates, not
    unavailable_property_ids directly -- monkeypatching the old function
    would silently stop exercising this failure path at all (confirmed: it
    still passed, vacuously, before this fix, since the mock was simply
    never invoked)."""
    property_ = Property(
        user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129008"
    )
    db_session.add(property_)
    await db_session.commit()

    from app.services import calendar_service

    async def _boom(db, property_ids, window_start, window_end, nights):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(calendar_service, "partial_availability_for_candidates", _boom)

    args = RecommendPropertiesArgs()
    check_in = date.today() + timedelta(days=5)
    check_out = check_in + timedelta(days=2)
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out
    )

    assert result.options[0].spoken_name == "Pine"
    assert result.partially_available == []


async def test_recommend_properties_classifies_full_partial_none_candidates(test_user, db_session):
    """Availability-first recommendations, Implementation 3: a candidate set
    with a mix of full/partial/none properties -- only 'full' properties
    land in options, 'partial' properties are surfaced separately (never
    silently dropped, never presented as a clean match), 'none' is excluded
    exactly as the old hard exclusion already did."""
    full = Property(user_id=test_user.id, name="Full Villa", base_price=3000, max_guests=4, exophone="+918011129301")
    partial = Property(
        user_id=test_user.id, name="Partial Villa", base_price=3200, max_guests=4, exophone="+918011129302"
    )
    none_ = Property(user_id=test_user.id, name="None Villa", base_price=3400, max_guests=4, exophone="+918011129303")
    db_session.add_all([full, partial, none_])
    await db_session.commit()
    for p in (full, partial, none_):
        await db_session.refresh(p)

    # A wide 10-day window, but the guest only wants a 3-night stay within
    # it -- nights is passed explicitly (distinct from the window span) so
    # "Partial Villa"'s conflict (days 3-5) still leaves sufficient gaps
    # (days 0-3 and 5-10) for a 3-night stay, genuinely testing the
    # partial-vs-none distinction rather than accidentally landing on "none"
    # via an unrelated nights value.
    check_in = date.today() + timedelta(days=30)
    check_out = check_in + timedelta(days=10)
    conflict_start = check_in + timedelta(days=3)
    conflict_end = check_in + timedelta(days=5)
    db_session.add_all(
        [
            Booking(property_id=partial.id, check_in=conflict_start, check_out=conflict_end, status="confirmed"),
            Booking(property_id=none_.id, check_in=check_in, check_out=check_out, status="confirmed"),
        ]
    )
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out, nights=3
    )

    option_names = [c.spoken_name for c in result.options]
    assert "Full Villa" in option_names
    assert "Partial Villa" not in option_names
    assert "None Villa" not in option_names

    assert len(result.partially_available) == 1
    assert result.partially_available[0].spoken_name == "Partial Villa"
    assert result.partially_available[0].conflicting_bookings == [(conflict_start, conflict_end)]


async def test_recommend_properties_nights_defaults_from_check_in_check_out_span(test_user, db_session):
    """When nights isn't explicitly passed, orchestrator.recommend_properties
    must fall back to (check_out - check_in).days as the required stay
    length for partial-availability classification -- the common case (a
    guest gave exact dates directly, no separate night count)."""
    property_ = Property(
        user_id=test_user.id, name="Short Stay Villa", base_price=3000, max_guests=4, exophone="+918011129304"
    )
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    check_in = date.today() + timedelta(days=30)
    check_out = check_in + timedelta(days=10)
    # A booking that leaves only a 4-day gap before it and a 3-day gap after
    # -- long enough for a 4-night stay derived from a wider window, but the
    # ACTUAL requested stay here is exactly (check_out - check_in) = 10
    # nights, which no gap can satisfy -- must classify as "none", not
    # "partial", confirming nights truly defaults to the full span and isn't
    # silently left as some other value.
    db_session.add(
        Booking(
            property_id=property_.id,
            check_in=check_in + timedelta(days=4),
            check_out=check_in + timedelta(days=7),
            status="confirmed",
        )
    )
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out
    )

    # "none" (no sufficient gap for the full 10-night stay) -- correctly
    # excluded from options, and NOT added to partially_available either
    # (that's reserved for "partial" status only, per this task's own status
    # design decision -- "none" and "partial" are deliberately different
    # outcomes, not both funneled into the same "flag it" bucket).
    assert result.options == []
    assert result.partially_available == []

    # Same booking data, but nights passed explicitly as a SHORTER stay than
    # the window span -- now the 4-day/3-day gaps DO satisfy a 3-night stay,
    # proving the prior assertion's "none" outcome came from the (check_out
    # - check_in) DEFAULT actually being applied, not from these bookings
    # being unconditionally "none" regardless of nights.
    result_shorter_stay = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out, nights=3
    )
    assert result_shorter_stay.options == []
    assert len(result_shorter_stay.partially_available) == 1
    assert result_shorter_stay.partially_available[0].spoken_name == "Short Stay Villa"


async def _city_property(db_session, user, name, city, exophone):
    property_ = Property(user_id=user.id, name=name, city=city, base_price=3000, max_guests=4, exophone=exophone)
    db_session.add(property_)
    await db_session.commit()
    return property_


async def test_unknown_area_miss_names_only_the_hosts_real_cities(test_user, db_session):
    """The 2026-11-13 call: nothing was found, the model was told to "offer another area" with no list
    and made up Koramangala and Juhu. The miss text must carry the real places and forbid the rest."""
    await _city_property(db_session, test_user, "Villa One", "Bengaluru", "+918011129101")
    await _city_property(db_session, test_user, "Villa Two", "Bengaluru", "+918011129102")
    await _city_property(db_session, test_user, "Villa Three", "Mumbai", "+918011129103")

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Narnia"), test_user.id
    )
    text = render_recommendation_text(result)

    assert result.not_found and result.missing_location == "Narnia"
    assert "We do have properties in: Bengaluru (2), Mumbai (1)." in text
    assert "Name only places from this list. Never name any other area." in text
    for invented in ("Juhu", "Koramangala", "Goa", "Delhi"):
        assert invented not in text


async def test_miss_lists_the_guests_own_city_first_even_with_fewer_properties(test_user, db_session):
    await _city_property(db_session, test_user, "Villa One", "Bengaluru", "+918011129111")
    await _city_property(db_session, test_user, "Villa Two", "Bengaluru", "+918011129112")
    await _city_property(db_session, test_user, "Villa Three", "Mumbai", "+918011129113")

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Juhu Mumbai"), test_user.id
    )

    assert result.available_areas == ["Mumbai (1)", "Bengaluru (2)"]


async def test_miss_with_no_cities_on_file_forbids_naming_any_place(test_user, db_session):
    property_ = Property(user_id=test_user.id, name="No City", base_price=3000, max_guests=4, exophone="+918011129121")
    db_session.add(property_)
    await db_session.commit()

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Narnia"), test_user.id
    )
    text = render_recommendation_text(result)

    assert "Do not name any other area or place" in text
    assert "We do have properties in" not in text


# --- locality search (Task 12): the area word lives in `locality`, not in the property name ---


def _located(user_id, name, city, locality, exophone, **extra):
    from app.services.property.locality import set_locality

    prop = Property(user_id=user_id, name=name, city=city, base_price=5000, max_guests=4, exophone=exophone, **extra)
    set_locality(prop, locality)
    return prop


async def _spacez_like_host(db_session, user_id):
    db_session.add_all(
        [
            _located(user_id, "Jugglers Rest", "Bengaluru", "Indiranagar", "+918011131001"),
            _located(user_id, "Classic Charm", "Bengaluru", "Koramangala", "+918011131002"),
            Property(user_id=user_id, name="Sunny Loft", city="Bengaluru", base_price=5000, max_guests=4, exophone="+918011131003"),
        ]
    )
    await db_session.commit()


async def test_every_spelling_of_an_area_finds_the_property_in_it(test_user, db_session):
    await _spacez_like_host(db_session, test_user.id)

    for phrase in ("Indiranagar", "Indira Nagar", "indiranagar ", "Indra Nagar"):
        result = await orchestrator.recommend_properties(
            db_session, RecommendPropertiesArgs(preferred_location=phrase), test_user.id
        )
        assert [o.spoken_name for o in result.options] == ["Jugglers Rest"], phrase
        assert result.resolved_location == "Indiranagar", phrase


async def test_a_landmark_mention_does_not_pull_in_a_property_in_another_area(test_user, db_session):
    db_session.add_all(
        [
            _located(test_user.id, "Jugglers Rest", "Bengaluru", "Indiranagar", "+918011131011"),
            _located(
                test_user.id, "Classic Charm", "Bengaluru", "Koramangala", "+918011131012",
                neighborhood_info="Twenty minutes from Indiranagar's restaurants.",
            ),
        ]
    )
    await db_session.commit()

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Indiranagar"), test_user.id
    )

    assert [o.spoken_name for o in result.options] == ["Jugglers Rest"]


async def test_a_property_with_no_locality_yet_is_still_found_by_the_old_text_match(test_user, db_session):
    db_session.add_all(
        [
            _located(test_user.id, "Jugglers Rest", "Bengaluru", "Indiranagar", "+918011131021"),
            Property(
                user_id=test_user.id, name="Not Yet Filled In", city="Bengaluru", base_price=5000, max_guests=4,
                exophone="+918011131022", neighborhood_info="Quiet lane in Indiranagar, near the metro.",
            ),
        ]
    )
    await db_session.commit()

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Indiranagar"), test_user.id
    )

    assert {o.spoken_name for o in result.options} == {"Jugglers Rest", "Not Yet Filled In"}


async def test_an_area_the_host_does_not_have_is_reported_missing_not_matched_to_the_city(test_user, db_session):
    await _spacez_like_host(db_session, test_user.id)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Juhu Bangalore"), test_user.id
    )

    assert result.options == []
    assert result.missing_location == "Juhu Bangalore"
    assert result.resolved_location == ""
    assert any("Bengaluru" in area for area in result.available_areas)


async def test_a_city_search_still_returns_every_property_in_the_city(test_user, db_session):
    await _spacez_like_host(db_session, test_user.id)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Bangalore"), test_user.id
    )

    assert len(result.options) == 3
    assert result.resolved_location == ""  # a city is not a locality; the slot keeps what the guest said


# --- offering real areas, and saying where each option is (Tasks 13 and 14) ---


def test_the_missing_area_offer_lists_the_areas_inside_each_city():
    areas = [("Bengaluru", 4), ("Goa", 2)]
    localities = {"bengaluru": [("Koramangala", 3), ("Indiranagar", 1)]}

    offered = orchestrator._areas_to_offer(areas, "Juhu Bangalore", localities)

    assert offered == ["Bengaluru (4; areas: Koramangala (3), Indiranagar (1))", "Goa (2)"]


def test_the_missing_area_offer_without_localities_is_just_the_cities():
    assert orchestrator._areas_to_offer([("Bengaluru", 4), ("Goa", 2)], None) == ["Bengaluru (4)", "Goa (2)"]


async def test_a_missing_area_reply_offers_the_hosts_real_areas(test_user, db_session):
    await _spacez_like_host(db_session, test_user.id)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Juhu"), test_user.id
    )
    text = render_recommendation_text(result)

    assert "Juhu" in text
    assert "Bengaluru (3; areas: Indiranagar (1), Koramangala (1))" in text


def _geo(label, distances, farther=False):
    from app.services.geo.gazetteer import ResolvedLocation
    from app.services.geo.search import GeoMatch

    return GeoMatch(ResolvedLocation(kind="place", label=label, lat=0.0, lng=0.0), set(distances), dict(distances), farther=farther)


def _prop(name, locality=None, city="Bengaluru"):
    from app.services.property.locality import set_locality

    prop = Property(id=__import__("uuid").uuid4(), name=name, city=city, base_price=1, max_guests=2)
    set_locality(prop, locality)
    return prop


def test_a_property_in_the_named_area_is_labelled_in_it():
    from app.services.property.retrieval.location_match import LocationMatch

    inside, other = _prop("Inside", "Indiranagar"), _prop("Other", "Koramangala")
    geo = _geo("Indiranagar", {inside.id: 1.0, other.id: 6.0})

    notes = orchestrator._location_notes(geo, [inside, other], LocationMatch("Bengaluru", "Indiranagar", "locality"))

    assert notes[inside.id] == "in Indiranagar"
    assert notes[other.id] == "about 6 km from Indiranagar in a straight line"  # something is in the area itself: no disclaimer


def test_a_nearby_option_says_there_is_none_in_the_place_itself_and_gives_a_distance_only():
    near = _prop("Near")
    geo = _geo("Kasol", {near.id: 12.4})

    notes = orchestrator._location_notes(geo, [near], None)

    assert notes[near.id] == "about 12 km from Kasol in a straight line -- we have no property in Kasol itself"
    assert "minute" not in notes[near.id] and "drive" not in notes[near.id]


def test_a_property_a_couple_of_km_away_is_not_declared_outside_the_place():
    near = _prop("Near")
    notes = orchestrator._location_notes(_geo("Kasol", {near.id: 2.0}), [near], None)
    assert notes[near.id] == "about 2 km from Kasol in a straight line"


def test_the_wider_ring_is_called_the_closest_we_have():
    far = _prop("Far")
    notes = orchestrator._location_notes(_geo("Kasol", {far.id: 90.0}, farther=True), [far], None)
    assert notes[far.id] == "the closest we have to Kasol, about 90 km away in a straight line"


def test_no_geo_and_no_area_means_no_notes():
    assert orchestrator._location_notes(None, [_prop("X")], None) == {}


async def test_a_miss_caused_by_the_budget_does_not_say_the_area_is_missing(test_user, db_session):
    """Found in the Phase 3 recheck: "We don't have a property in or near Indiranagar ... we do have
    Indiranagar (1)". An area the host has, searched with a budget nothing fits, is a budget miss."""
    await _spacez_like_host(db_session, test_user.id)

    result = await orchestrator.recommend_properties(
        db_session,
        RecommendPropertiesArgs(preferred_location="Indiranagar", budget_amount=100, budget_basis="per_night"),
        test_user.id,
    )
    text = render_recommendation_text(result)

    assert result.options == []
    assert result.missing_location == "" and result.available_areas == []
    assert "don't have a property in or near" not in text
    assert "budget" in text.lower()


async def test_a_miss_in_a_hosted_city_caused_by_an_amenity_or_group_does_not_say_the_city_is_missing(test_user, db_session):
    await _spacez_like_host(db_session, test_user.id)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Bangalore", budget_amount=100, budget_basis="per_night"), test_user.id
    )

    assert result.missing_location == ""


async def test_the_area_check_fails_open_to_describing_the_area_as_missing(test_user, db_session, monkeypatch):
    await _spacez_like_host(db_session, test_user.id)

    def _boom(*args, **kwargs):
        raise RuntimeError("filter builder broke")

    real = orchestrator.filter_builder.build_base_filters
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if kwargs.get("resolved") is not None and calls["n"] > 1:  # the second call is the area check
            _boom()
        return real(*args, **kwargs)

    monkeypatch.setattr(orchestrator.filter_builder, "build_base_filters", flaky)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(preferred_location="Indiranagar", budget_amount=100, budget_basis="per_night"), test_user.id
    )

    assert result.missing_location == "Indiranagar"  # the old behaviour, never a crash


# --- a plain city query must not read like a nearby-area query (found replaying on the located Spacez data) ---


def test_a_property_in_the_named_city_gets_no_distance_note():
    from app.services.property.retrieval.location_match import LocationMatch

    inside = _prop("Inside", city="Bengaluru")
    geo = _geo("Bangalore", {inside.id: 12.0})

    notes = orchestrator._location_notes(geo, [inside], LocationMatch(city="Bengaluru", via="city"))

    assert notes == {}  # "about 12 km from Bangalore ... no property in Bangalore itself" would be false


def test_a_property_in_another_city_still_gets_its_distance_when_a_city_was_named():
    from app.services.property.retrieval.location_match import LocationMatch

    inside, village = _prop("Inside", city="Kasol"), _prop("Village", city="Jari")
    geo = _geo("Kasol", {inside.id: 1.0, village.id: 7.0})

    notes = orchestrator._location_notes(geo, [inside, village], LocationMatch(city="Kasol", via="city"))

    assert inside.id not in notes
    assert notes[village.id] == "about 7 km from Kasol in a straight line"  # something is in the city, so no "none in X itself"


def test_in_city_properties_sort_before_nearby_ones_and_a_plain_search_keeps_pure_distance():
    near_village, in_city = _prop("Village", city="Jari"), _prop("InCity", city="Kasol")
    geo = _geo("Kasol", {near_village.id: 1.0, in_city.id: 28.0})

    assert orchestrator._geo_order(in_city, geo, "Kasol") < orchestrator._geo_order(near_village, geo, "Kasol")
    # No city named (an area or a village): nearest first, as before.
    assert orchestrator._geo_order(near_village, geo, None) < orchestrator._geo_order(in_city, geo, None)
