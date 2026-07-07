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


async def test_new_lead_defaults_to_open_status(test_user, test_call_session, db_session):
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Priya")
    assert lead.status == "open"


async def test_upsert_lead_persists_status_and_occasion(test_user, test_call_session, db_session):
    lead = await lead_service.upsert_lead(
        db_session,
        test_user.id,
        test_call_session.id,
        status="contacted",
        occasion="Guest mentioned it's their anniversary, wants a quiet room",
    )
    assert lead.status == "contacted"
    assert lead.occasion == "Guest mentioned it's their anniversary, wants a quiet room"


async def test_backfill_lead_never_overwrites_status(test_user, test_call_session, db_session):
    # backfill_lead is only supposed to fill BLANK fields -- status always
    # has a value (defaults to "open"), so backfill must never touch it.
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, status="booked")
    await lead_service.backfill_lead(db_session, test_call_session.id, status="open")

    await db_session.refresh(lead)
    assert lead.status == "booked"
