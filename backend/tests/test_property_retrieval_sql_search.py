from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.retrieval import filter_builder, sql_search


async def test_run_sql_search_orders_by_price_and_limits_to_three(test_user, db_session):
    properties = [
        Property(user_id=test_user.id, name=f"Unit{i}", base_price=1000 * i, max_guests=2, exophone=f"+9180{i:08d}")
        for i in range(1, 6)
    ]
    db_session.add_all(properties)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, combo_note = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert len(results) == 3
    assert [p.name for p in results] == ["Unit1", "Unit2", "Unit3"]
    assert combo_note == ""


async def test_run_sql_search_falls_back_to_smaller_units_for_large_groups(test_user, db_session):
    unit_a = Property(user_id=test_user.id, name="Unit A", base_price=3000, max_guests=3, exophone="+918011110001")
    unit_b = Property(user_id=test_user.id, name="Unit B", base_price=3200, max_guests=3, exophone="+918011110002")
    db_session.add_all([unit_a, unit_b])
    await db_session.commit()

    args = RecommendPropertiesArgs(num_guests=6)
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, combo_note = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert {p.name for p in results} == {"Unit A", "Unit B"}
    assert "book two of them together" in combo_note


async def test_run_sql_search_applies_landmark_boost(test_user, db_session):
    near = Property(
        user_id=test_user.id, name="Near", base_price=4000, max_guests=2, exophone="+918011110003",
        landmarks=[{"name": "Thalassa", "distance_minutes": 5}],
    )
    far = Property(user_id=test_user.id, name="Far", base_price=3000, max_guests=2, exophone="+918011110004")
    db_session.add_all([near, far])
    await db_session.commit()

    args = RecommendPropertiesArgs(near_landmark="Thalassa")
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, _ = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert [p.name for p in results] == ["Near", "Far"]


async def test_zero_price_property_excluded_from_recommendations(test_user, db_session):
    """Phase 2.0 (documentation/agent-conversation-improvement.md, catalogue
    item C2): a property with base_price=0 must never be recommended --
    confirmed live, this was previously pitched to a guest as "free of
    charge per night" during recommend_properties, a different path from
    the already-fixed ₹0 guard in handle_get_pricing/handle_negotiate_rate."""
    zero_priced = Property(
        user_id=test_user.id, name="Zero Villa", base_price=0, max_guests=4, exophone="+918011110005"
    )
    real = Property(
        user_id=test_user.id, name="Real Villa", base_price=4000, max_guests=4, exophone="+918011110006"
    )
    db_session.add_all([zero_priced, real])
    await db_session.commit()

    args = RecommendPropertiesArgs()
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, _ = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert [p.name for p in results] == ["Real Villa"]


async def test_zero_price_property_with_exact_airbnb_pricing_is_not_excluded(test_user, db_session):
    """A property with exact_airbnb_pricing=True gets its real price from a
    live SearchApi fetch at get_pricing time regardless of base_price -- a
    stale/unset base_price=0 there is harmless and must not hide an
    otherwise-real, working listing from recommendations."""
    live_priced = Property(
        user_id=test_user.id,
        name="Live Priced Villa",
        base_price=0,
        max_guests=4,
        exophone="+918011110007",
        exact_airbnb_pricing=True,
        airbnb_listing_id="12345",
    )
    db_session.add(live_priced)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, _ = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert [p.name for p in results] == ["Live Priced Villa"]


async def test_run_sql_search_widens_pool_for_amenity_match_beyond_three_cheapest(test_user, db_session):
    """Recommendation conversations ("Phase X"): required_amenities is now a
    soft boost (apply_amenity_boost), not a hard WHERE filter -- so a
    property that matches the requested amenity but isn't among the 3
    cheapest must still be able to surface in the final, still-3-item
    result. Without widening the candidate pool past 3 before boosting,
    the boost would have nothing to promote -- the cheapest 3 would already
    be fetched and capped before amenity ranking ever ran."""
    cheap_no_match = Property(
        user_id=test_user.id, name="Cheap1", base_price=1000, max_guests=2,
        exophone="+918011110010", amenity_tags=[],
    )
    cheap_no_match_2 = Property(
        user_id=test_user.id, name="Cheap2", base_price=1100, max_guests=2,
        exophone="+918011110011", amenity_tags=[],
    )
    cheap_no_match_3 = Property(
        user_id=test_user.id, name="Cheap3", base_price=1200, max_guests=2,
        exophone="+918011110012", amenity_tags=[],
    )
    pricier_with_pool = Property(
        user_id=test_user.id, name="PoolVilla", base_price=2000, max_guests=2,
        exophone="+918011110013", amenity_tags=["pool"],
    )
    db_session.add_all([cheap_no_match, cheap_no_match_2, cheap_no_match_3, pricier_with_pool])
    await db_session.commit()

    args = RecommendPropertiesArgs(required_amenities=["pool"])
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, _ = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert len(results) == 3
    assert results[0].name == "PoolVilla"


async def test_run_sql_search_without_amenities_uses_normal_pool_of_three(test_user, db_session):
    """Baseline: no required_amenities means no widened pool and no boost --
    the 4th-cheapest property (which happens to have every amenity) must NOT
    appear, confirming the widening is conditional on required_amenities
    actually being set, not always-on."""
    cheap1 = Property(
        user_id=test_user.id, name="Cheap1", base_price=1000, max_guests=2,
        exophone="+918011110014", amenity_tags=[],
    )
    cheap2 = Property(
        user_id=test_user.id, name="Cheap2", base_price=1100, max_guests=2,
        exophone="+918011110015", amenity_tags=[],
    )
    cheap3 = Property(
        user_id=test_user.id, name="Cheap3", base_price=1200, max_guests=2,
        exophone="+918011110016", amenity_tags=[],
    )
    pricier_with_pool = Property(
        user_id=test_user.id, name="PoolVilla", base_price=2000, max_guests=2,
        exophone="+918011110017", amenity_tags=["pool"],
    )
    db_session.add_all([cheap1, cheap2, cheap3, pricier_with_pool])
    await db_session.commit()

    args = RecommendPropertiesArgs()
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, _ = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert {p.name for p in results} == {"Cheap1", "Cheap2", "Cheap3"}


async def test_zero_price_property_excluded_even_with_budget_filter_active(test_user, db_session):
    """base_price=0 trivially passes ANY budget<= check, so the exclusion
    must be its own unconditional clause, not folded into the budget
    filter -- confirmed by testing with a budget filter active, not just
    the no-filter case above."""
    zero_priced = Property(
        user_id=test_user.id, name="Zero Villa", base_price=0, max_guests=4, exophone="+918011110008"
    )
    real = Property(
        user_id=test_user.id, name="Real Villa", base_price=4000, max_guests=4, exophone="+918011110009"
    )
    db_session.add_all([zero_priced, real])
    await db_session.commit()

    args = RecommendPropertiesArgs(budget=5000)
    base_stmt = filter_builder.build_base_filters(args, test_user.id)
    results, _ = await sql_search.run_sql_search(db_session, base_stmt, args)

    assert [p.name for p in results] == ["Real Villa"]
