"""Covers Guest Memory's write path (memory-architecture-plan.md section 1):
call_service.get_or_create_guest_profile's (phone, host_id) scoping, and
guest_memory_service.update_guest_memory_from_call pulling from the call's
own Lead row (never a new LLM summarization call)."""

import uuid

from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.services import call_service, guest_memory_service


async def test_same_phone_different_hosts_gets_separate_profiles(db_session, test_user):
    other_host_id = uuid.uuid4()
    # Bypass the FK requirement for this isolated unit test by using a
    # second real host row -- simpler to just create one.
    from app.auth.security import hash_password
    from app.models.user import User

    other_host = User(id=other_host_id, email=f"other-{uuid.uuid4().hex[:8]}@example.com", hashed_password=hash_password("x"))
    db_session.add(other_host)
    await db_session.commit()

    guest_a = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id)
    guest_b = await call_service.get_or_create_guest_profile(db_session, "+919999999999", other_host_id)

    assert guest_a.id != guest_b.id
    assert guest_a.host_id == test_user.id
    assert guest_b.host_id == other_host_id


async def test_same_phone_same_host_returns_existing_profile(db_session, test_user):
    guest_a = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id)
    guest_b = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id)
    assert guest_a.id == guest_b.id


async def test_update_guest_memory_with_no_lead_is_a_noop(db_session, test_user):
    """A call where the agent captured nothing (no Lead row exists, or it
    was deleted by delete_if_empty) must not error and must not create a
    phantom summary."""
    guest = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id)
    await guest_memory_service.update_guest_memory_from_call(
        db_session, call_session_id=uuid.uuid4(), guest_profile_id=guest.id, property_id=None, property_name=None
    )
    await db_session.refresh(guest)
    assert guest.total_stays == 0
    assert guest.conversation_summaries == []


async def test_update_guest_memory_pulls_from_leads_conversation_summary(
    db_session, test_user, test_property, test_call_session
):
    guest = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id)
    lead = Lead(
        user_id=test_user.id,
        call_session_id=test_call_session.id,
        conversation_summary="Guest wants a 2-night stay, asked about parking.",
        lead_temperature="warm",
        next_follow_up="Call back with pricing tomorrow",
        properties_discussed=["Test Villa"],
    )
    db_session.add(lead)
    await db_session.commit()

    await guest_memory_service.update_guest_memory_from_call(
        db_session,
        call_session_id=test_call_session.id,
        guest_profile_id=guest.id,
        property_id=test_property.id,
        property_name="Test Villa",
    )

    await db_session.refresh(guest)
    assert guest.total_stays == 1
    assert guest.last_property_id == test_property.id
    assert guest.last_outcome == "warm"
    assert guest.last_follow_up == "Call back with pricing tomorrow"
    assert len(guest.conversation_summaries) == 1
    entry = guest.conversation_summaries[0]
    assert entry["summary"] == "Guest wants a 2-night stay, asked about parking."
    assert entry["property_name"] == "Test Villa"
    assert entry["lead_temperature"] == "warm"

    await db_session.refresh(lead)
    assert lead.guest_profile_id == guest.id


async def test_update_guest_memory_caps_conversation_summaries(db_session, test_user, test_call_session):
    guest = GuestProfile(
        phone="+919999999999",
        host_id=test_user.id,
        total_stays=guest_memory_service.MAX_CONVERSATION_SUMMARIES,
        conversation_summaries=[{"summary": f"call {i}"} for i in range(guest_memory_service.MAX_CONVERSATION_SUMMARIES)],
    )
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    lead = Lead(user_id=test_user.id, call_session_id=test_call_session.id, conversation_summary="the newest call")
    db_session.add(lead)
    await db_session.commit()

    await guest_memory_service.update_guest_memory_from_call(
        db_session,
        call_session_id=test_call_session.id,
        guest_profile_id=guest.id,
        property_id=None,
        property_name=None,
    )

    await db_session.refresh(guest)
    assert len(guest.conversation_summaries) == guest_memory_service.MAX_CONVERSATION_SUMMARIES
    assert guest.conversation_summaries[-1]["summary"] == "the newest call"
    assert guest.conversation_summaries[0]["summary"] == "call 1"  # oldest ("call 0") dropped


async def test_update_guest_memory_without_conversation_summary_still_bumps_stays(
    db_session, test_user, test_call_session
):
    """A lead with no conversation_summary (e.g. only phone/name captured)
    still counts as a real stay, just doesn't add a summary entry."""
    guest = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id)
    lead = Lead(user_id=test_user.id, call_session_id=test_call_session.id, guest_name="Priya")
    db_session.add(lead)
    await db_session.commit()

    await guest_memory_service.update_guest_memory_from_call(
        db_session,
        call_session_id=test_call_session.id,
        guest_profile_id=guest.id,
        property_id=None,
        property_name=None,
    )

    await db_session.refresh(guest)
    assert guest.total_stays == 1
    assert guest.conversation_summaries == []
    assert guest.name == "Priya"


async def test_update_guest_memory_overwrites_stale_name_with_latest_call(
    db_session, test_user, test_call_session
):
    # Regression: GuestProfile.name was never synced from a call's own
    # Lead.guest_name, so a name set once (e.g. very first test call, or a
    # manual dashboard edit) stayed stale forever. A guest who gave a
    # different name on this call ("Deepika") must overwrite whatever the
    # profile said before ("Shagun Verma") -- confirmed live, 2026-07-21.
    guest = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id, name="Shagun Verma")
    lead = Lead(user_id=test_user.id, call_session_id=test_call_session.id, guest_name="Deepika")
    db_session.add(lead)
    await db_session.commit()

    await guest_memory_service.update_guest_memory_from_call(
        db_session,
        call_session_id=test_call_session.id,
        guest_profile_id=guest.id,
        property_id=None,
        property_name=None,
    )

    await db_session.refresh(guest)
    assert guest.name == "Deepika"


async def test_update_guest_memory_keeps_existing_name_when_guest_doesnt_restate_it(
    db_session, test_user, test_call_session
):
    # A returning guest who doesn't restate their name this call (Guest
    # Memory already knows it, so the agent isn't expected to ask again --
    # see system_prompt.py's _guest_memory_section) must not have their
    # correct on-file name blanked out or overwritten by a call with no
    # guest_name captured.
    guest = await call_service.get_or_create_guest_profile(db_session, "+919999999999", test_user.id, name="Deepika")
    lead = Lead(user_id=test_user.id, call_session_id=test_call_session.id, conversation_summary="Asked about check-in.")
    db_session.add(lead)
    await db_session.commit()

    await guest_memory_service.update_guest_memory_from_call(
        db_session,
        call_session_id=test_call_session.id,
        guest_profile_id=guest.id,
        property_id=None,
        property_name=None,
    )

    await db_session.refresh(guest)
    assert guest.name == "Deepika"
