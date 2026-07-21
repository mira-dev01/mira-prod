from datetime import date, timedelta

from app.models.guest_profile import GuestProfile
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


async def test_backfill_lead_fills_blank_guest_name_from_guest_memory(test_user, test_call_session, db_session):
    # A returning guest who doesn't restate their name mid-call (Guest Memory
    # already knows it -- see system_prompt.py's _guest_memory_section)
    # should still show the correct name on the Lead itself, not just via
    # CallSession's computed guest_name fallback. Mirrors the existing
    # caller_number -> phone backfill in app/voice/pipeline.py.
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, budget=5000)
    assert lead.guest_name is None

    await lead_service.backfill_lead(db_session, test_call_session.id, guest_name="Deepika")

    await db_session.refresh(lead)
    assert lead.guest_name == "Deepika"


async def test_backfill_lead_never_overwrites_guest_stated_name(test_user, test_call_session, db_session):
    # If the guest DID give a name this call (via update_lead), that's
    # authoritative -- backfill must never replace it with the Guest Memory
    # profile's on-file name (e.g. from an earlier call).
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Deepika")

    await lead_service.backfill_lead(db_session, test_call_session.id, guest_name="Shagun Verma")

    await db_session.refresh(lead)
    assert lead.guest_name == "Deepika"


async def _guest_profile(db_session, host_id, phone="+919999911111"):
    guest = GuestProfile(host_id=host_id, phone=phone, total_stays=1)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)
    return guest


async def test_get_active_booking_finds_booked_lead_with_future_checkout(test_user, db_session):
    guest = await _guest_profile(db_session, test_user.id)
    lead = await lead_service.upsert_lead(
        db_session,
        test_user.id,
        None,
        guest_profile_id=guest.id,
        status="booked",
        check_in=date.today() + timedelta(days=5),
        check_out=date.today() + timedelta(days=7),
        properties_discussed=["Alpine Ridge Chalet"],
    )

    booking = await lead_service.get_active_booking(db_session, guest.id, test_user.id)
    assert booking is not None
    assert booking.id == lead.id


async def test_get_active_booking_ignores_past_stays(test_user, db_session):
    guest = await _guest_profile(db_session, test_user.id)
    await lead_service.upsert_lead(
        db_session,
        test_user.id,
        None,
        guest_profile_id=guest.id,
        status="booked",
        check_in=date.today() - timedelta(days=10),
        check_out=date.today() - timedelta(days=8),
    )

    booking = await lead_service.get_active_booking(db_session, guest.id, test_user.id)
    assert booking is None


async def test_get_active_booking_ignores_non_booked_status(test_user, db_session):
    guest = await _guest_profile(db_session, test_user.id)
    await lead_service.upsert_lead(
        db_session, test_user.id, None, guest_profile_id=guest.id, status="open"
    )

    booking = await lead_service.get_active_booking(db_session, guest.id, test_user.id)
    assert booking is None


async def test_get_active_booking_includes_booking_with_no_dates_yet(test_user, db_session):
    # A lead can be marked "booked" by the host before exact dates are on
    # file -- still worth surfacing rather than requiring both.
    guest = await _guest_profile(db_session, test_user.id)
    lead = await lead_service.upsert_lead(
        db_session, test_user.id, None, guest_profile_id=guest.id, status="booked"
    )

    booking = await lead_service.get_active_booking(db_session, guest.id, test_user.id)
    assert booking is not None
    assert booking.id == lead.id


async def test_get_active_booking_none_without_guest_profile_id(test_user, db_session):
    booking = await lead_service.get_active_booking(db_session, None, test_user.id)
    assert booking is None
