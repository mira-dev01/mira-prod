"""CRM records for the Lead Agent flow. update_lead (the voice tool) calls
upsert_lead repeatedly during a single call as the agent learns more about
the guest; the dashboard's Leads page reads back through list_leads/get_lead.
"""

import uuid
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange
from app.models.call_session import CallSession
from app.models.lead import Lead

# A returning guest's follow-up call reuses their existing Lead only while
# it's still an unresolved, in-progress inquiry. Once the host marks it
# "booked" or "closed", that inquiry is done -- the next call starts a fresh
# Lead (a new booking cycle), rather than mutating a resolved record.
_REUSABLE_LEAD_STATUSES = ("open", "contacted")


async def _get_or_create_lead_for_call(
    db: AsyncSession,
    user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None,
    guest_name_hint: str | None,
) -> Lead | None:
    """Resolves the Lead this call's tool calls should read/write, creating
    one only if nothing already applies. Confirmed live 2026-07-21: a
    returning guest's follow-up calls about the same still-open inquiry each
    created their own separate Lead row, fragmenting one guest's engagement
    across multiple dashboard cards instead of one.

    Lookup order:
    1. This call_session already resolved/created a lead earlier THIS same
       call (CallSession.lead_id set by an earlier tool call this session) --
       reuse it directly, no further lookup.
    2. Otherwise, look for the guest's most recent still-open/contacted Lead
       for this host (matched via guest_profile_id, itself scoped by
       phone+host -- see GuestProfile) and reuse that instead of creating a
       new one.
    3. Otherwise, create a brand new Lead, exactly as before.

    Safety guard on step 2: a shared/family phone reused for a genuinely
    different person must never silently overwrite the existing lead's
    identity -- only reused if that lead has no name yet, or its name
    matches (case-insensitively) what this call has already stated. This
    only guards the FIRST tool call of a session that resolves a lead; a
    name given later in the same call that conflicts with an
    already-resolved reused lead isn't re-checked -- in practice the guest's
    name is captured immediately when volunteered (see GOLDEN_RULES), so
    this is decided correctly before any other tool call needs a lead.

    call_session_id=None is valid, not just tolerated: RecoveryService
    (app/services/recovery_service.py) resolves/reuses a guest's lead for a
    rejected call that never got a CallSession at all (see
    app/voice/pipeline.py's BUSY_RECOVERY branch, which returns before
    get_or_create_call_session is ever reached) -- step 1 above simply has
    nothing to check in that case, but step 2's guest_profile_id-based reuse
    still applies fully, so a guest busy-rejected more than once still
    lands on the same still-open Lead rather than fragmenting into one row
    per rejected attempt. Every pre-existing caller here always passes a
    real call_session_id from a live call, so this is additive, not a
    behavior change for the in-call path.
    """
    call_session = None
    if call_session_id is not None:
        call_session = await db.get(CallSession, call_session_id)
        if call_session is not None and call_session.lead_id is not None:
            existing = await db.get(Lead, call_session.lead_id)
            if existing is not None:
                return existing

    lead = None
    if guest_profile_id is not None:
        stmt = (
            select(Lead)
            .where(
                Lead.guest_profile_id == guest_profile_id,
                Lead.user_id == user_id,
                Lead.status.in_(_REUSABLE_LEAD_STATUSES),
            )
            .order_by(Lead.updated_at.desc())
        )
        for candidate in (await db.scalars(stmt)).all():
            if (
                not candidate.guest_name
                or not guest_name_hint
                or candidate.guest_name.strip().lower() == guest_name_hint.strip().lower()
            ):
                lead = candidate
                break

    if lead is None:
        lead = Lead(user_id=user_id, call_session_id=call_session_id)
        db.add(lead)
        await db.flush()  # assign lead.id without committing the whole unit of work yet

    if call_session is not None and call_session.lead_id != lead.id:
        call_session.lead_id = lead.id

    return lead


async def upsert_lead(
    db: AsyncSession,
    user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None = None,
    **fields,
) -> Lead:
    lead = await _get_or_create_lead_for_call(
        db, user_id, call_session_id, guest_profile_id, guest_name_hint=fields.get("guest_name")
    )
    if lead is None:
        lead = Lead(user_id=user_id, call_session_id=call_session_id)
        db.add(lead)

    # A real, answered call (call_session_id is not None -- recovery_service.py's
    # own busy-rejection upsert always passes None, see that module's own
    # comment) that reuses a Lead still armed for busy-recovery availability
    # follow-up (busy_recovery_availability_status set) means the guest got
    # through to Mira for real, on this exact Lead, since the busy call that
    # armed it. Sending "Mira is available now" after that would be a stale,
    # confusing message -- the guest already knows Mira is available, they're
    # talking to her right now (or just finished). Cleared unconditionally
    # here, before **fields below, regardless of whether THIS call's own
    # tool calls happen to touch next_follow_up (process_availability_recovery's
    # own next_follow_up guard only catches an explicit update_lead call
    # during the callback -- many real calls resolve without one, e.g. the
    # guest asks a quick question and hangs up satisfied, which would
    # otherwise leave this lead wrongly claimable by a later, unrelated
    # lease-release event for a different busy caller). recovery_reason
    # itself is untouched -- "why this lead originally needed recovery"
    # stays true and historical even after the guest gets through.
    if call_session_id is not None and lead.busy_recovery_availability_status is not None:
        lead.busy_recovery_availability_status = None
        lead.busy_recovery_claimed_at = None

    # guest_profile_id is consumed above for the reuse lookup, not passed
    # through **fields -- still needs applying to the lead itself, same
    # "only set if actually given" semantics as every other field below.
    if guest_profile_id is not None:
        lead.guest_profile_id = guest_profile_id

    for key, value in fields.items():
        if value is not None:
            setattr(lead, key, value)

    await db.commit()
    await db.refresh(lead)
    return lead


async def backfill_lead_from_engagement(
    db: AsyncSession,
    user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    property_name: str,
    check_in: date,
    check_out: date,
    num_guests: int | None,
    guest_profile_id: uuid.UUID | None = None,
) -> None:
    """System-level safety net: creates/backfills a Lead the moment a guest
    engages meaningfully with a specific property + dates (get_pricing,
    negotiate_rate, check_calendar), independent of the LLM ever calling
    update_lead itself. Traced live: real booking calls were going through a
    full price negotiation and ending with zero Lead row, because the model
    said its escalation/booking phrases without reliably following through
    with the actual update_lead/escalate_to_host tool call -- a live LLM
    function-calling reliability gap, not a prompt clarity one. This makes a
    Lead's existence not depend on that call happening at all.

    Deliberately narrow to only what these three tool calls always carry
    (property, dates, guest count) -- never overwrites a field the guest/LLM
    already set via update_lead (same blank-only semantics as backfill_lead
    above), and never sets guest_name/phone/email/lead_temperature, which
    only mean something if actually given by the guest.
    """
    if call_session_id is None:
        return
    lead = await _get_or_create_lead_for_call(db, user_id, call_session_id, guest_profile_id, guest_name_hint=None)
    if lead is None:
        return

    changed = False
    if property_name not in (lead.properties_discussed or []):
        lead.properties_discussed = [*(lead.properties_discussed or []), property_name]
        changed = True
    if not lead.check_in:
        lead.check_in = check_in
        changed = True
    if not lead.check_out:
        lead.check_out = check_out
        changed = True
    if num_guests and not lead.num_guests:
        lead.num_guests = num_guests
        changed = True

    # Commit unconditionally, not just `if changed` -- _get_or_create_lead_for_call
    # may have just created a new lead or linked this call_session.lead_id for
    # the first time, which needs persisting even if none of the fields above
    # happened to change (e.g. reusing an existing lead that already has this
    # exact property/dates on file).
    await db.commit()


async def backfill_lead(db: AsyncSession, call_session_id: uuid.UUID | None, **fields) -> None:
    """Fill only currently-blank fields on the lead THIS call is associated
    with (e.g. the caller's phone from Exotel, or the property a Guest
    Support call was about) at call end. Never creates a lead: a call where
    the agent captured nothing must leave no row rather than an empty
    'unknown guest' phantom, and anything the guest actually stated during
    the call (via update_lead) is authoritative and must not be overwritten
    here. Looked up via CallSession.lead_id rather than Lead.call_session_id
    directly, so this also correctly reaches a lead REUSED from an earlier
    call (see _get_or_create_lead_for_call), not just one this exact call
    originally created.
    """
    if call_session_id is None:
        return
    call_session = await db.get(CallSession, call_session_id)
    if call_session is None or call_session.lead_id is None:
        return
    lead = await db.get(Lead, call_session.lead_id)
    if lead is None:
        return
    changed = False
    for key, value in fields.items():
        # `not getattr(...)` treats None and the default empty list/"" as
        # blank -- so an already-populated field (a phone the guest gave, a
        # non-empty properties_discussed) is left untouched.
        if value and not getattr(lead, key, None):
            setattr(lead, key, value)
            changed = True
    if changed:
        await db.commit()


async def delete_if_empty(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Safety net for a lead that got created by a stray tool call on a call
    that never actually became a conversation (e.g. escalate_to_host firing
    on a connection that dropped instantly). Leads are no longer created up
    front (see app/voice/pipeline.py), so in the normal case there's nothing
    to clean; this just guards against a near-empty row slipping through and
    looking like a phantom entry on the Leads page. Naturally safe for a
    REUSED lead too -- one with any real history already has at least one of
    the checked fields set, so it's never considered "empty" here regardless
    of whether this particular call added anything new.
    """
    if call_session_id is None:
        return
    call_session = await db.get(CallSession, call_session_id)
    if call_session is None or call_session.lead_id is None:
        return
    lead = await db.get(Lead, call_session.lead_id)
    if lead is None:
        return
    has_data = any(
        getattr(lead, field) for field in ("guest_name", "phone", "email", "check_in", "lead_temperature")
    )
    if not has_data:
        await db.delete(lead)
        await db.commit()


async def delete_for_unqualified_call(db: AsyncSession, call_session_id: uuid.UUID | None) -> None:
    """Called from on_pipeline_finished after end-of-call classification
    (app/services/call_classification_service.py), for any call_type NOT in
    QUALIFIED_CALL_TYPES (JUNK/INCOMPLETE/UNKNOWN). Deletes the Lead row --
    unlike delete_if_empty (which only clears near-empty phantom rows), a
    junk/incomplete classification overrides whatever the live tool calls
    captured, since the full-transcript end-of-call review is necessarily
    more informed than the LLM's real-time in-call judgment.

    EXCEPT when this call's lead was REUSED from an earlier, legitimate call
    (lead.call_session_id != this call_session_id) -- a bad/junk
    classification on one follow-up call must never delete a returning
    guest's whole prior history. In that case, just detach this call from
    the lead (clear this call's own lead_id) and leave the shared lead
    itself untouched. No-op if this call has no associated lead at all.
    """
    if call_session_id is None:
        return
    call_session = await db.get(CallSession, call_session_id)
    if call_session is None or call_session.lead_id is None:
        return
    lead = await db.get(Lead, call_session.lead_id)
    if lead is None:
        return
    if lead.call_session_id != call_session_id:
        call_session.lead_id = None
        await db.commit()
        return
    await db.delete(lead)
    await db.commit()


async def list_leads(db: AsyncSession, user_id: uuid.UUID, date_range: DateRange | None = None) -> list[Lead]:
    stmt = select(Lead).where(Lead.user_id == user_id).order_by(Lead.created_at.desc())
    if date_range is not None:
        if date_range.since is not None:
            stmt = stmt.where(Lead.created_at >= date_range.since)
        if date_range.until is not None:
            stmt = stmt.where(Lead.created_at < date_range.until)
    return list((await db.scalars(stmt)).all())


async def get_owned_lead(db: AsyncSession, lead_id: uuid.UUID, user_id: uuid.UUID) -> Lead | None:
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.user_id != user_id:
        return None
    return lead


async def get_active_booking(db: AsyncSession, guest_profile_id: uuid.UUID | None, host_id: uuid.UUID) -> Lead | None:
    """Most recent Lead the host has marked status="booked" for this guest
    (Lead.status is host-managed from the Leads page, see app/models/lead.py)
    that isn't a past stay -- feeds system_prompt.py's guest-memory section
    so a returning guest calling about their upcoming/current stay gets
    recognized by name, property, and dates rather than treated as a fresh
    caller. check_out is None-or-future: a lead can be marked booked before
    the guest ever gave exact dates, and that's still worth surfacing.
    Returns None if there's no resolvable guest profile or no such lead --
    never raises, this is purely additive context for the prompt."""
    if guest_profile_id is None:
        return None
    stmt = (
        select(Lead)
        .where(
            Lead.guest_profile_id == guest_profile_id,
            Lead.user_id == host_id,
            Lead.status == "booked",
        )
        .order_by(Lead.updated_at.desc())
    )
    leads = (await db.scalars(stmt)).all()
    today = date.today()
    for lead in leads:
        if lead.check_out is None or lead.check_out >= today:
            return lead
    return None


async def objection_conversion_analytics(
    db: AsyncSession, user_id: uuid.UUID, date_range: DateRange | None = None
) -> dict:
    """Conversion rate by CallSummary.objection_tags (docs/tasks/
    building-intelligence.md, Implementation 4) -- read-only, purely a
    display/analytics aggregate. Deliberately does NOT write anything back
    into NegotiationRule/PricingRule/pricing_engine.py: see this task's own
    "Explicitly not in scope" note -- an automatic, unreviewed pricing
    change from an aggregate correlation is a correctness/trust risk this
    codebase's own smart_pricing_service.py already declined for the same
    reason (never feeds comparable pricing into get_pricing/negotiate_rate
    automatically). A host reads this and can act on it manually (e.g. by
    editing their own NegotiationRule policy text).

    Multi-tag handling (Implementation 2's review flagged this ambiguity
    explicitly): a call carrying two tags (e.g. PRICE_TOO_HIGH and
    DATES_UNAVAILABLE) counts toward BOTH tags' numerator/denominator --
    achieved via jsonb_array_elements_text producing one row per tag per
    call, so each tag's rate reflects "of calls where this specific
    friction occurred," not a partitioning of calls into exclusive buckets.

    Resolved-vs-unresolved handling (carried over from Implementation 2's
    review: a tag fires whether or not the objection was actually overcome,
    e.g. PRICE_TOO_HIGH appears on both a lost negotiation and a
    successfully-discounted booking): rather than silently excluding
    converted calls from a tag's denominator (which would answer a
    different, narrower question than "conversion rate for calls with this
    friction") or silently folding resolution into the same number, this
    keeps every tagged call in the denominator -- the literal, honestly
    labeled question -- and additionally reports resolved_count (tagged
    calls that still converted) and unresolved_count (tagged calls that
    didn't) as a separate, visible breakdown per tag, so a host isn't shown
    a flat rate that hides whether the objection was actually a dealbreaker.

    Takes date_range (app/api/v1/common.py's DateRange, same helper
    analytics_summary already uses) rather than a bucket: str granularity
    param -- this function's output has no time-series/trend dimension
    (unlike quality_event_analytics's over_time), so a bucket param would
    be dead weight; a start/end window filter is what this task actually
    needs.

    Uses raw SQL (sqlalchemy.text, still fully parameter-bound -- never
    string-interpolated) for the tag-unnesting query specifically, not
    SQLAlchemy Core's .table_valued(): reproduced consistently (8/8 runs, a
    fresh connection each time) against the real dev DB that
    .table_valued()'s query -- both the default comma-join and an explicit
    `JOIN ... ON true` -- raises "column anon_1.tag does not exist" via
    asyncpg at PREPARE time, before any parameters bind. NOTE: an
    independent review of this task, on the same SQLAlchemy 2.0.51/asyncpg
    0.31.0 versions against the same DB, reported the .table_valued()
    version succeeding for them -- this discrepancy was not root-caused
    (checked and ruled out: not a Neon pgbouncer/pooled-endpoint artifact,
    since this connection string uses Neon's direct, non "-pooler" host;
    not session/rollback-state-dependent, since the repro above used a
    fresh session per attempt). Treat this as "known to fail
    deterministically in this environment," not "impossible to ever
    succeed" -- if you're revisiting this function and .table_valued()
    works for you, that's plausible; the raw-SQL version is kept regardless
    since it's simpler to reason about and already proven correct (see
    Implementation 4's task-file verification notes), not because the ORM
    path is understood to be broken everywhere. The one combination
    confirmed to always execute is a literal SQL
    `JOIN jsonb_array_elements_text(...) AS alias(column_name) ON true`
    with the column name declared inside the function-call alias itself,
    which SQLAlchemy Core's table_valued() has no option to emit. All
    user-controlled values (user_id, date bounds) are still passed as
    bound :params, never interpolated into the SQL string.
    """
    is_booked_sql = "CASE WHEN leads.status = 'booked' THEN 1 ELSE 0 END"
    window_sql = ""
    params: dict = {"user_id": user_id}
    if date_range is not None:
        if date_range.since is not None:
            window_sql += " AND call_sessions.created_at >= :since"
            params["since"] = date_range.since
        if date_range.until is not None:
            window_sql += " AND call_sessions.created_at < :until"
            params["until"] = date_range.until

    by_tag_stmt = text(
        f"""
        SELECT tag.value AS tag, count(*) AS total, sum({is_booked_sql}) AS booked
        FROM call_sessions
        JOIN jsonb_array_elements_text(call_sessions.ai_summary -> 'objection_tags') AS tag(value) ON true
        LEFT OUTER JOIN leads ON call_sessions.lead_id = leads.id
        WHERE call_sessions.user_id = :user_id AND call_sessions.ai_summary IS NOT NULL{window_sql}
        GROUP BY tag.value
        ORDER BY count(*) DESC
        """
    )
    by_tag_rows = (await db.execute(by_tag_stmt, params)).all()
    by_tag = [
        {
            "tag": row.tag,
            "total_calls": row.total,
            "resolved_count": row.booked,
            "unresolved_count": row.total - row.booked,
            "conversion_rate": round(row.booked / row.total, 3) if row.total else None,
        }
        for row in by_tag_rows
        # NO_OBJECTION is a valid tag value but not an objection -- it
        # belongs in the baseline, not the objection breakdown, since it
        # would otherwise always show a 100%-ish rate that isn't a "this
        # objection is fine" signal, just "there was no objection."
        if row.tag != "NO_OBJECTION"
    ]

    baseline_stmt = text(
        f"""
        SELECT count(*) AS total, sum({is_booked_sql}) AS booked
        FROM call_sessions
        LEFT OUTER JOIN leads ON call_sessions.lead_id = leads.id
        WHERE call_sessions.user_id = :user_id AND call_sessions.ai_summary IS NOT NULL{window_sql}
        """
    )
    baseline_row = (await db.execute(baseline_stmt, params)).one()
    baseline_total = baseline_row.total or 0
    baseline_booked = baseline_row.booked or 0
    baseline = {
        "total_calls": baseline_total,
        "resolved_count": baseline_booked,
        "unresolved_count": baseline_total - baseline_booked,
        "conversion_rate": round(baseline_booked / baseline_total, 3) if baseline_total else None,
    }

    return {"by_tag": by_tag, "baseline": baseline}
