import uuid

from sqlalchemy import select

from app.config import settings
from app.models.call_session import CallSession
from app.schemas.tool import RequestHostTransferArgs
from app.services import tool_handlers
from app.voice import handoff_signal


async def test_request_host_transfer_falls_back_to_escalation_with_no_property(
    test_user, db_session
):
    # Lead Agent call (no property_id) -- no handoff listener is ever
    # registered for one, so this must not attempt a transfer at all.
    args = RequestHostTransferArgs(reason="wants to talk to someone")
    result = await tool_handlers.handle_request_host_transfer(
        db_session, args, call_session_id=None, property_id=None, host_user_id=test_user.id
    )
    assert "flagged" in result.lower() or "follow up" in result.lower()


async def test_request_host_transfer_falls_back_when_host_has_no_phone(
    test_property, test_call_session, db_session
):
    # test_user fixture never sets .phone -- confirms the no-usable-phone
    # branch falls back to escalation instead of attempting a dead-end
    # transfer.
    args = RequestHostTransferArgs(reason="wants to talk to the owner")
    result = await tool_handlers.handle_request_host_transfer(
        db_session,
        args,
        call_session_id=test_call_session.id,
        property_id=test_property.id,
        host_user_id=test_property.user_id,
    )
    assert "flagged" in result.lower() or "follow up" in result.lower()
    assert "connecting" not in result.lower()

    # Confirm no handoff was actually claimed.
    refreshed = await db_session.get(CallSession, test_call_session.id)
    assert refreshed.handoff_status is None


async def test_request_host_transfer_claims_handoff_and_signals_pipeline(
    test_property, test_call_session, db_session
):
    test_property.owner.phone = "9876543210"
    await db_session.commit()

    handoff_signal.register_call(test_call_session.id)
    try:
        args = RequestHostTransferArgs(reason="wants to talk to the owner")
        result = await tool_handlers.handle_request_host_transfer(
            db_session,
            args,
            call_session_id=test_call_session.id,
            property_id=test_property.id,
            host_user_id=test_property.user_id,
        )
        assert "connecting" in result.lower()

        refreshed = await db_session.get(CallSession, test_call_session.id)
        assert refreshed.handoff_status == "requested"

        # The registered Event should now be set -- confirms request_handoff
        # actually fired, not just the DB claim.
        event = handoff_signal._handoff_events[test_call_session.id]
        assert event.is_set()
    finally:
        handoff_signal.unregister_call(test_call_session.id)


async def test_request_host_transfer_does_not_double_claim_an_already_requested_handoff(
    test_property, test_call_session, db_session
):
    test_property.owner.phone = "9876543210"
    test_call_session.handoff_status = "requested"
    await db_session.commit()

    args = RequestHostTransferArgs(reason="wants to talk to the owner")
    result = await tool_handlers.handle_request_host_transfer(
        db_session,
        args,
        call_session_id=test_call_session.id,
        property_id=test_property.id,
        host_user_id=test_property.user_id,
    )
    # Should not error, and should not re-attempt/duplicate the claim.
    assert isinstance(result, str) and result

    refreshed = await db_session.get(CallSession, test_call_session.id)
    assert refreshed.handoff_status == "requested"


async def test_request_host_transfer_never_checks_twilio_enabled(
    test_property, test_call_session, db_session, monkeypatch
):
    # Regression guard for the plan's explicit Twilio-independence
    # requirement -- disabling Twilio must not affect whether a transfer is
    # attempted.
    monkeypatch.setattr(settings, "twilio_enabled", False)
    test_property.owner.phone = "9876543210"
    await db_session.commit()

    handoff_signal.register_call(test_call_session.id)
    try:
        args = RequestHostTransferArgs(reason="wants to talk to the owner")
        result = await tool_handlers.handle_request_host_transfer(
            db_session,
            args,
            call_session_id=test_call_session.id,
            property_id=test_property.id,
            host_user_id=test_property.user_id,
        )
        assert "connecting" in result.lower()
        refreshed = await db_session.get(CallSession, test_call_session.id)
        assert refreshed.handoff_status == "requested"
    finally:
        handoff_signal.unregister_call(test_call_session.id)
