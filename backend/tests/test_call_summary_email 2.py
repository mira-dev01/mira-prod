import uuid

from app.services import call_summary_email, lead_service


async def test_send_call_summary_email_noop_for_unknown_call(db_session, caplog):
    # Should never raise, even for a call_session_id that doesn't exist.
    await call_summary_email.send_call_summary_email(db_session, uuid.uuid4())


async def test_send_call_summary_email_noop_for_none(db_session):
    # call_session_id can legitimately be None (e.g. a construction failure
    # before a CallSession row ever existed) -- must be a silent no-op.
    await call_summary_email.send_call_summary_email(db_session, None)


async def test_send_call_summary_email_skips_smtp_when_unconfigured(test_call_session, db_session, caplog):
    # Test env has no SMTP configured -- confirms the function reaches the
    # send_email call (i.e. successfully loaded the CallSession with its
    # eager-loaded relationships without raising MissingGreenlet) and
    # degrades to the documented "skipped" log line, exactly like every
    # other best-effort email send in this codebase.
    import logging

    with caplog.at_level(logging.INFO):
        await call_summary_email.send_call_summary_email(db_session, test_call_session.id)
    assert any("Call summary email" in r.message and "skipped" in r.message for r in caplog.records)


async def test_send_call_summary_email_reads_lead_temperature_without_lazy_load_error(
    test_call_session, test_property, db_session
):
    # Regression guard: session.lead/session.property/session.guest_name
    # access must not raise MissingGreenlet -- confirms the selectinload
    # eager-loading actually covers every relationship this function (and
    # CallSession's own computed properties) touches.
    lead = await lead_service.upsert_lead(
        db_session,
        test_property.user_id,
        test_call_session.id,
        phone="9999999999",
        conversation_summary="Guest wants to book",
    )
    lead.lead_temperature = "hot"
    test_call_session.lead_id = lead.id
    await db_session.commit()

    # Should complete without raising, regardless of SMTP config.
    await call_summary_email.send_call_summary_email(db_session, test_call_session.id)
