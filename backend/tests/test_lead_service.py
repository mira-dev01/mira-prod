import uuid
from datetime import date, timedelta

from app.models.call_session import CallSession
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


async def _call_session(db_session, host_id, property_id=None):
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=host_id,
        property_id=property_id,
        caller_number="+919999911111",
        status="in_progress",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def test_upsert_lead_reuses_open_lead_for_returning_guest_new_call(test_user, db_session):
    # The actual feature requested 2026-07-21: a returning guest's next
    # call/follow-up should land on the SAME Lead entry, not a new one.
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    lead_1 = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, guest_name="Deepika", budget=8000
    )
    assert lead_1.status == "open"

    call_2 = await _call_session(db_session, test_user.id)
    lead_2 = await lead_service.upsert_lead(
        db_session, test_user.id, call_2.id, guest_profile_id=guest.id, num_guests=2
    )

    assert lead_2.id == lead_1.id
    assert lead_2.guest_name == "Deepika"  # from call 1, never lost
    assert lead_2.num_guests == 2  # from call 2, merged onto the same row

    leads = await lead_service.list_leads(db_session, test_user.id)
    assert len([l for l in leads if l.guest_profile_id == guest.id]) == 1


async def test_upsert_lead_does_not_reuse_booked_lead(test_user, db_session):
    # Once the host marks a lead "booked", that inquiry is resolved -- the
    # next call is a new booking cycle, not a continuation.
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    booked_lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, guest_name="Deepika", status="booked"
    )

    call_2 = await _call_session(db_session, test_user.id)
    new_lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_2.id, guest_profile_id=guest.id, guest_name="Deepika"
    )

    assert new_lead.id != booked_lead.id
    assert new_lead.status == "open"


async def test_upsert_lead_does_not_reuse_closed_lead(test_user, db_session):
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    closed_lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, status="closed"
    )

    call_2 = await _call_session(db_session, test_user.id)
    new_lead = await lead_service.upsert_lead(db_session, test_user.id, call_2.id, guest_profile_id=guest.id)

    assert new_lead.id != closed_lead.id


async def test_upsert_lead_does_not_reuse_when_stated_name_conflicts(test_user, db_session):
    # A shared/family phone used by a genuinely different person must never
    # silently overwrite the existing lead's identity.
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    deepika_lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, guest_name="Deepika"
    )

    call_2 = await _call_session(db_session, test_user.id)
    priya_lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_2.id, guest_profile_id=guest.id, guest_name="Priya"
    )

    assert priya_lead.id != deepika_lead.id
    await db_session.refresh(deepika_lead)
    assert deepika_lead.guest_name == "Deepika"  # untouched


async def test_upsert_lead_reuses_when_name_matches_case_insensitively(test_user, db_session):
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    lead_1 = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, guest_name="Deepika"
    )

    call_2 = await _call_session(db_session, test_user.id)
    lead_2 = await lead_service.upsert_lead(
        db_session, test_user.id, call_2.id, guest_profile_id=guest.id, guest_name="deepika"
    )

    assert lead_2.id == lead_1.id


async def test_upsert_lead_within_same_call_still_reuses_via_call_session(test_user, test_call_session, db_session):
    # Regression guard: the ordinary case (several tool calls within ONE
    # call) must keep landing on the same lead, same as before this change.
    lead_1 = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Asha")
    lead_2 = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, budget=6000)

    assert lead_1.id == lead_2.id


async def test_delete_for_unqualified_call_deletes_lead_it_originated(test_user, test_call_session, db_session):
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, guest_name="Rohan")

    await lead_service.delete_for_unqualified_call(db_session, test_call_session.id)

    from app.models.lead import Lead

    assert await db_session.get(Lead, lead.id) is None


async def test_delete_for_unqualified_call_never_deletes_a_reused_lead(test_user, db_session):
    # Regression: a bad/junk follow-up call must never delete a returning
    # guest's whole prior history just because THIS call classified poorly.
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, guest_name="Deepika", budget=8000
    )

    call_2 = await _call_session(db_session, test_user.id)
    await lead_service.upsert_lead(db_session, test_user.id, call_2.id, guest_profile_id=guest.id)

    await lead_service.delete_for_unqualified_call(db_session, call_2.id)

    from app.models.lead import Lead

    surviving = await db_session.get(Lead, lead.id)
    assert surviving is not None
    assert surviving.guest_name == "Deepika"

    await db_session.refresh(call_2)
    assert call_2.lead_id is None  # this call is detached, but the lead itself survives


async def test_backfill_lead_reaches_a_reused_lead(test_user, db_session):
    guest = await _guest_profile(db_session, test_user.id)
    call_1 = await _call_session(db_session, test_user.id)
    lead = await lead_service.upsert_lead(
        db_session, test_user.id, call_1.id, guest_profile_id=guest.id, guest_name="Deepika"
    )

    call_2 = await _call_session(db_session, test_user.id)
    await lead_service.upsert_lead(db_session, test_user.id, call_2.id, guest_profile_id=guest.id)

    await lead_service.backfill_lead(db_session, call_2.id, phone="9876543210")

    await db_session.refresh(lead)
    assert lead.phone == "9876543210"


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
