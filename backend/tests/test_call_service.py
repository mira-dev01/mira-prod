from app.services import call_service


async def test_get_property_by_twilio_number_matches(db_session, test_property):
    test_property.twilio_number = "+14155550100"
    await db_session.commit()

    found = await call_service.get_property_by_twilio_number(db_session, "+14155550100")
    assert found is not None
    assert found.id == test_property.id


async def test_get_property_by_twilio_number_does_not_match_exophone(db_session, test_property):
    # Regression: the two lookup functions must stay independent -- a Twilio
    # dialed number must never accidentally match a property via its Exotel
    # exophone column, and vice versa.
    test_property.exophone = "+912246184496"
    test_property.twilio_number = None
    await db_session.commit()

    found = await call_service.get_property_by_twilio_number(db_session, "+912246184496")
    assert found is None


async def test_get_property_by_twilio_number_returns_none_for_unset_number(db_session):
    assert await call_service.get_property_by_twilio_number(db_session, None) is None
    assert await call_service.get_property_by_twilio_number(db_session, "") is None


async def test_get_user_by_twilio_lead_number_matches(db_session, test_user):
    test_user.twilio_lead_number = "+14155550199"
    await db_session.commit()

    found = await call_service.get_user_by_twilio_lead_number(db_session, "+14155550199")
    assert found is not None
    assert found.id == test_user.id


async def test_get_user_by_twilio_lead_number_does_not_match_lead_exophone(db_session, test_user):
    test_user.lead_exophone = "+911141189038"
    test_user.twilio_lead_number = None
    await db_session.commit()

    found = await call_service.get_user_by_twilio_lead_number(db_session, "+911141189038")
    assert found is None
