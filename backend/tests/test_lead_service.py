from app.services import lead_service


async def test_delete_if_empty_removes_lead_with_no_data(test_user, test_call_session, db_session):
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id)
    assert lead.guest_name is None

    await lead_service.delete_if_empty(db_session, test_call_session.id)

    leads = await lead_service.list_leads(db_session, test_user.id)
    assert all(l.call_session_id != test_call_session.id for l in leads)


async def test_delete_if_empty_keeps_lead_with_data(test_user, test_call_session, db_session):
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Rohan")

    await lead_service.delete_if_empty(db_session, test_call_session.id)

    leads = await lead_service.list_leads(db_session, test_user.id)
    assert any(l.call_session_id == test_call_session.id for l in leads)


async def test_delete_if_empty_handles_no_call_session_id(db_session):
    # Should be a no-op, not raise, when there's nothing to look up.
    await lead_service.delete_if_empty(db_session, None)
