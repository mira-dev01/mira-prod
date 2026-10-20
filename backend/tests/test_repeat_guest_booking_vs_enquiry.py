"""Phase 4E, Step 5/8 -- regression tests documenting the CURRENT,
CONFIRMED behavior of GuestProfile.total_stays and its use as the
repeat-guest/loyalty signal in pricing_engine.py. This phase's own
investigation (see the Phase 4E final report) found that total_stays
increments once per call that produces a non-empty Lead row (see
app/services/guest_memory_service.py:91, called from
app/voice/pipeline.py's on_pipeline_finished) -- NOT on any
confirmed/completed booking. Lead.status can be manually set to "booked"
by the host (app/schemas/lead.py's LeadStatus enum), but nothing in the
codebase automatically verifies a real reservation occurred, and nothing
currently wires Lead.status into pricing_engine's repeat-guest check at
all (confirmed: get_active_booking, the one function that reads
Lead.status == "booked", is used only for in-call "you have an upcoming
stay" prompt context -- app/prompts/system_prompt.py -- never for
discount eligibility).

This file does NOT implement booking-based eligibility -- that is an
explicit PRODUCT DECISION REQUIRED per this phase's hard-stop #2 ("'Loyal
customer' cannot be safely mapped to booking history"). These tests exist
to (a) prove the gap precisely, with the exact numbers the brief's Step 8
scenarios ask for, and (b) act as a tripwire: if a future change silently
starts treating an enquiry as a qualifying booking (or vice versa) without
an explicit product decision, one of these tests will fail and force that
change to be deliberate, not accidental.
"""

from datetime import date, timedelta

from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.negotiation_rule import NegotiationRule
from app.services.pricing_engine import negotiate_rate


def _next_weekday(start: date, weekday: int) -> date:
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


async def _approved_repeat_guest_rule(db_session, host_id, percent=7):
    db_session.add(
        NegotiationRule(host_id=host_id, rule_type="discount_repeat_guest", discount_percent=percent, status="approved")
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Step 8, scenario 1: guest with 0 bookings, 3 enquiries -- NOT eligible for
# a booking-based loyalty policy in principle, but CONFIRMED here that
# today's system has no way to distinguish this from 3 real bookings, since
# total_stays is a call counter, not a booking counter.
# ---------------------------------------------------------------------------


async def test_guest_profile_total_stays_increments_on_enquiries_not_just_bookings(
    test_property, test_user, db_session
):
    """CONFIRMED CURRENT BEHAVIOR (not a product decision this test makes):
    a guest with total_stays=3, where all 3 calls were plain enquiries
    (no Lead ever reached status="booked"), still triggers the
    discount_repeat_guest rule exactly as if all 3 had been real bookings.
    This is the precise gap Phase 4E's investigation identified -- this
    test documents it rather than silently accepting or silently "fixing"
    it, since fixing it requires the PRODUCT DECISION this phase flags."""
    await _approved_repeat_guest_rule(db_session, test_user.id)

    guest = GuestProfile(phone="+919999911111", host_id=test_user.id, total_stays=3)
    db_session.add(guest)
    # Three Lead rows for this guest, none ever marked "booked" -- pure
    # enquiries (the default status new leads get, per Lead.status's own
    # server_default="open").
    for _ in range(3):
        db_session.add(Lead(user_id=test_user.id, guest_profile_id=None, status="open"))
    await db_session.commit()
    await db_session.refresh(guest)

    monday = _next_weekday(date.today(), 0)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        monday + timedelta(days=2),
        guest_offer=1,
        guest_loyalty="new",
        host_id=test_user.id,
        guest_profile_id=guest.id,
    )
    # Today's system grants the repeat-guest discount -- confirmed, not
    # asserted as "correct." A future booking-verified implementation may
    # change this once the product decision is made.
    expected_floor = round(result.asking_price * (1 - 7 / 100), 2)
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# Step 8, scenario 2: Lead.status == "booked" exists as a real, host-settable
# field, but is CONFIRMED to have zero wiring into the repeat-guest signal.
# ---------------------------------------------------------------------------


async def test_lead_status_booked_has_no_effect_on_repeat_guest_eligibility(test_property, test_user, db_session):
    """CONFIRMED CURRENT BEHAVIOR: even a guest with an EXPLICITLY
    host-confirmed booking (Lead.status="booked") gets no special
    treatment from pricing_engine's repeat-guest check -- only
    GuestProfile.total_stays is consulted (app/services/pricing_engine.py's
    _is_repeat_guest_for_host), which never reads Lead.status at all. This
    is the other half of the Phase 4E investigation's finding: the one
    genuine "a real booking happened" signal that DOES exist in the
    codebase (Lead.status="booked") is completely disconnected from
    negotiation/pricing today."""
    await _approved_repeat_guest_rule(db_session, test_user.id)

    guest = GuestProfile(phone="+919999922222", host_id=test_user.id, total_stays=0)
    db_session.add(guest)
    db_session.add(Lead(user_id=test_user.id, guest_profile_id=None, status="booked"))
    await db_session.commit()
    await db_session.refresh(guest)

    monday = _next_weekday(date.today(), 0)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        monday + timedelta(days=2),
        guest_offer=1,
        guest_loyalty="new",
        host_id=test_user.id,
        guest_profile_id=guest.id,
    )
    # total_stays=0 -> not a repeat guest by today's only actual signal,
    # REGARDLESS of the Lead.status="booked" row existing for this host.
    # Falls through to the default loyalty-bonus formula, not the 7% rule.
    expected_floor = round(result.asking_price * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# Step 8, scenario 3: qualifying booking count matching a host's configured
# threshold -- CONFIRMED behavior is threshold=2 (hardcoded), not
# host-configurable, and still call-based rather than booking-based.
# ---------------------------------------------------------------------------


async def test_repeat_guest_threshold_is_hardcoded_at_two_not_host_configurable(test_property, test_user, db_session):
    """CONFIRMED CURRENT BEHAVIOR: the eligibility threshold ("how many
    stays make someone a repeat guest") is a fixed >= 2 comparison in
    pricing_engine._is_repeat_guest_for_host, not read from any host
    policy field. A host who wants a stricter or looser threshold (e.g.
    Example D from Step 15: "at least 2 confirmed bookings," or a
    hypothetical host wanting 3+) has no way to configure this today --
    this is a second, narrower product gap than the enquiry-vs-booking
    question, documented here for completeness rather than silently
    assumed fixed by this phase."""
    await _approved_repeat_guest_rule(db_session, test_user.id)
    guest = GuestProfile(phone="+919999933333", host_id=test_user.id, total_stays=2)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    monday = _next_weekday(date.today(), 0)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        monday + timedelta(days=2),
        guest_offer=1,
        guest_loyalty="new",
        host_id=test_user.id,
        guest_profile_id=guest.id,
    )
    expected_floor = round(result.asking_price * (1 - 7 / 100), 2)
    assert result.counter_offer == expected_floor  # total_stays==2 qualifies -- the hardcoded threshold


# ---------------------------------------------------------------------------
# Step 8, scenario 4/Step 6: portfolio-wide scope -- CONFIRMED the existing
# signal is already portfolio-wide (host-scoped, not property-scoped),
# because GuestProfile itself is uniqued on (phone, host_id), not per
# property.
# ---------------------------------------------------------------------------


async def test_repeat_guest_signal_is_already_portfolio_wide_not_property_scoped(db_session, test_user):
    """CONFIRMED CURRENT BEHAVIOR (partially satisfies Step 6): GuestProfile
    is uniqued on (phone, host_id) -- app/models/guest_profile.py's own
    UniqueConstraint -- meaning a guest's total_stays already aggregates
    across every property of this host, never scoped to "the property
    they're currently asking about." A booking/call at Property A already
    contributes to total_stays checked while the guest is now asking about
    Property B. This is confirmed structurally, not asserted as fully
    solving Step 6 -- the SAME total_stays count applies uniformly to
    every discount_repeat_guest rule a host approves; there is no way for
    a host to configure a DIFFERENT (e.g. property-scoped-only) loyalty
    rule, since the only existing eligibility signal has no such
    parameter."""
    from app.models.property import Property
    from app.services.pricing_engine import _is_repeat_guest_for_host

    property_a = Property(user_id=test_user.id, name="Villa A", city="Goa", exophone="+918000099930", base_price=4000, max_guests=4)
    property_b = Property(user_id=test_user.id, name="Villa B", city="Goa", exophone="+918000099931", base_price=4000, max_guests=4)
    db_session.add_all([property_a, property_b])

    # ONE GuestProfile row for this host -- not one per property (the
    # UniqueConstraint structurally prevents per-property guest profiles).
    guest = GuestProfile(phone="+919999944444", host_id=test_user.id, total_stays=2, last_property_id=None)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)
    await db_session.refresh(property_a)
    await db_session.refresh(property_b)

    # The SAME guest_profile_id resolves to "repeat guest" regardless of
    # which property is passed to negotiate_rate elsewhere -- confirmed
    # directly against the eligibility function itself, since it takes no
    # property argument at all.
    is_repeat = await _is_repeat_guest_for_host(db_session, guest.id)
    assert is_repeat is True
