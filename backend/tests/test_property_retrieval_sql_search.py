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
