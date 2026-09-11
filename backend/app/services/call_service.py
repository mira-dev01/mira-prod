import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_quality_event import CallQualityEvent
from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.property import Property
from app.models.user import User
from app.schemas.call_classification import ClassificationResult
from app.schemas.call_summary import CallSummary

if TYPE_CHECKING:
    from app.voice.conversation_quality import ConversationQuality

logger = logging.getLogger(__name__)

# Placeholder caller identity for the dashboard's "test in browser" feature
# (no real phone number exists for a WebRTC test call). The frontend renders
# this specific value as a "Browser test" label wherever it shows up --
# Calls, Leads, Guests -- rather than treating it as a real phone number.
BROWSER_TEST_CALLER_NUMBER = "browser-test"


async def get_property_by_number(db: AsyncSession, dialed_number: str | None) -> Property | None:
    if not dialed_number:
        return None
    return await db.scalar(select(Property).where(Property.exophone == dialed_number))


async def get_user_by_lead_number(db: AsyncSession, dialed_number: str | None) -> User | None:
    if not dialed_number:
        return None
    return await db.scalar(select(User).where(User.lead_exophone == dialed_number))


async def get_property_by_twilio_number(db: AsyncSession, dialed_number: str | None) -> Property | None:
    """Twilio equivalent of get_property_by_number above -- separate function
    reading a separate column (Property.twilio_number) so nothing about the
    Exotel routing path above is touched."""
    if not dialed_number:
        return None
    return await db.scalar(select(Property).where(Property.twilio_number == dialed_number))


async def get_user_by_twilio_lead_number(db: AsyncSession, dialed_number: str | None) -> User | None:
    """Twilio equivalent of get_user_by_lead_number above."""
    if not dialed_number:
        return None
    return await db.scalar(select(User).where(User.twilio_lead_number == dialed_number))


def extract_caller_number(call: dict) -> str | None:
    return call.get("from") or None


async def get_or_create_guest_profile(
    db: AsyncSession, caller_number: str | None, host_id: uuid.UUID | None, name: str | None = None
) -> GuestProfile | None:
    """Scoped by (phone, host_id) -- see memory-architecture-plan.md section
    1 -- so the same phone number calling two different hosts on Mira gets
    two independent profiles, never one shared/leaked across hosts.
    host_id should always be resolvable by the time this is called (every
    voice-pipeline entry point knows the host before it needs a guest
    profile); it's optional here only to tolerate a caller_number with no
    dialed-number match at all, which already short-circuits before this in
    every real call path.
    """
    if not caller_number:
        return None

    guest = await db.scalar(
        select(GuestProfile).where(GuestProfile.phone == caller_number, GuestProfile.host_id == host_id)
    )
    if guest is not None:
        return guest

    guest = GuestProfile(phone=caller_number, host_id=host_id, name=name, total_stays=0)
    db.add(guest)
    await db.commit()
    await db.refresh(guest)
    return guest


async def get_or_create_call_session(
    db: AsyncSession,
    exotel_call_id: str | None,
    property_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None,
    caller_number: str | None,
    user_id: uuid.UUID | None = None,
) -> CallSession:
    session = None
    if exotel_call_id:
        session = await db.scalar(select(CallSession).where(CallSession.exotel_call_id == exotel_call_id))

    if session is not None:
        return session

    session = CallSession(
        exotel_call_id=exotel_call_id,
        user_id=user_id,
        property_id=property_id,
        guest_profile_id=guest_profile_id,
        caller_number=caller_number,
        status="in_progress",
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def record_busy_rejected_call(
    db: AsyncSession,
    *,
    exotel_call_id: str | None,
    property_id: uuid.UUID | None,
    caller_number: str | None,
    host_user_id: uuid.UUID,
) -> CallSession | None:
    """Persist a Calls-tab row for a call that CallCoordinator rejected as
    BUSY_RECOVERY -- the host/property was already on a live call, so this
    guest never reached Mira/the LLM and no pipeline CallSession would
    otherwise exist for them. Called from BOTH the Exotel and Twilio
    BUSY_RECOVERY branches in app/voice/pipeline.py (shared here rather than
    duplicated -- same instinct as Busy Call Recovery reusing
    get_or_create_guest_profile / upsert_lead, per CLAUDE.md).

    status="failed": the same bucket _map_exotel_status puts a real
    busy/no-answer Exotel call in, and it keeps this rejection out of
    analytics' completed_calls count -- a busy-rejected call is a missed
    call, not a completed one. call_type="MISSED_AGENT_BUSY" is set directly
    (no classify_call -- there is no transcript to read). started_at and
    ended_at are both "now": the rejection already happened by the time this
    is called, and a call that never connected has no meaningful duration.

    Best-effort by contract: returns None (and logs) on any failure rather
    than raising, since the caller's Busy Call Recovery WhatsApp flow must
    fire regardless of whether this bookkeeping row got written.
    """
    try:
        guest = await get_or_create_guest_profile(db, caller_number, host_user_id)
        session = await get_or_create_call_session(
            db,
            exotel_call_id=exotel_call_id,
            property_id=property_id,
            guest_profile_id=guest.id if guest else None,
            caller_number=caller_number,
            user_id=host_user_id,
        )
        now = datetime.now(timezone.utc)
        session.started_at = session.started_at or now
        session.ended_at = now
        session.status = "failed"
        await db.commit()
        await set_call_classification(
            db,
            session.id,
            ClassificationResult(
                call_type="MISSED_AGENT_BUSY",
                confidence=1.0,
                reason="Host/property already on a live call; rejected and routed to Busy Call Recovery.",
            ),
        )
        return session
    except Exception:
        logger.exception(
            "Failed to record MISSED_AGENT_BUSY CallSession for host %s, call %s", host_user_id, exotel_call_id
        )
        return None


async def attach_exotel_call(
    db: AsyncSession,
    exotel_call_id: str,
    caller_number: str | None,
    dialed_number: str | None,
    status: str | None,
    recording_url: str | None,
) -> CallSession:
    """Upsert the call_sessions record for Exotel's call-status callback.

    The voice pipeline (app/voice/pipeline.py) creates the session for calls
    that reach the AI, keyed by the same `exotel_call_id` Exotel reports here
    as `CallSid` -- so this just updates that row. Calls that never reach the
    AI (busy/no-answer/failed) get their session created here instead."""
    session = await db.scalar(select(CallSession).where(CallSession.exotel_call_id == exotel_call_id))

    if session is None:
        property_ = await get_property_by_number(db, dialed_number)
        session = CallSession(
            exotel_call_id=exotel_call_id,
            user_id=property_.user_id if property_ else None,
            property_id=property_.id if property_ else None,
            caller_number=caller_number,
            status="in_progress",
            started_at=datetime.now(timezone.utc),
        )
        db.add(session)

    if status:
        session.status = _map_exotel_status(status)
    if recording_url:
        session.recording_url = recording_url

    await db.commit()
    await db.refresh(session)
    return session


async def set_call_classification(
    db: AsyncSession, call_session_id: uuid.UUID | None, classification: ClassificationResult
) -> None:
    """Persists the end-of-call classification (app/services/
    call_classification_service.py), called from on_pipeline_finished right
    after finalize_call_session. No-op if the call session can't be
    resolved -- mirrors finalize_call_session's own None-tolerant shape."""
    if call_session_id is None:
        return
    session = await db.get(CallSession, call_session_id)
    if session is None:
        return

    session.call_type = classification.call_type
    session.classification_confidence = classification.confidence
    session.classification_reason = classification.reason
    await db.commit()


async def set_call_summary(db: AsyncSession, call_session_id: uuid.UUID | None, summary: CallSummary) -> None:
    """Persists the end-of-call structured summary (app/services/
    call_summary_service.py), called from on_pipeline_finished alongside
    set_call_classification. No-op if the call session can't be resolved --
    mirrors set_call_classification's own None-tolerant shape."""
    if call_session_id is None:
        return
    session = await db.get(CallSession, call_session_id)
    if session is None:
        return

    session.ai_summary = summary.model_dump()
    await db.commit()


async def record_quality_events(
    db: AsyncSession, call_session_id: uuid.UUID | None, quality: "ConversationQuality"
) -> None:
    """Persists ConversationQuality's in-memory ValidationResults (app/voice/
    conversation_quality.py) once a call has ended, for cross-call
    aggregation only (docs/tasks/building-intelligence.md, Implementation 1)
    -- ConversationQuality itself stays exactly as observational/per-call as
    documented in its own module docstring; nothing here reads this data
    back into a live call. Called from on_pipeline_finished alongside
    set_call_classification/set_call_summary. No-op if there are no
    validations to record. Never raises: a telemetry write must not be able
    to break call teardown, matching the fail-open discipline
    _update_guest_memory (app/voice/pipeline.py) already follows for its own
    best-effort, post-call write.
    """
    if call_session_id is None or not quality.validations:
        return
    try:
        # Mirrors set_call_classification/set_call_summary's own
        # None-tolerant existence check -- cheaper than relying on the FK
        # constraint to reject a stale/nonexistent call_session_id, and
        # avoids an insert-then-rollback on every such call.
        session = await db.get(CallSession, call_session_id)
        if session is None:
            return
        db.add_all(
            [
                CallQualityEvent(
                    call_session_id=call_session_id,
                    rule=result.rule,
                    severity=result.severity,
                    confidence=result.confidence,
                    turn_index=result.turn_index,
                    processing_time_ms=result.processing_time_ms,
                    metadata_json=result.metadata,
                )
                for result in quality.validations
            ]
        )
        await db.commit()
    except Exception:
        logger.exception("Failed to record quality events for call_session_id=%s", call_session_id)
        await db.rollback()


def _map_exotel_status(exotel_status: str) -> str:
    completed = {"completed", "answered"}
    failed = {"failed", "busy", "no-answer", "canceled"}
    status = exotel_status.lower()
    if status in completed:
        return "completed"
    if status in failed:
        return "failed"
    return "in_progress"


async def finalize_call_session(
    db: AsyncSession,
    call_session_id: uuid.UUID,
    transcript: str | None,
    status: str = "completed",
) -> CallSession | None:
    session = await db.get(CallSession, call_session_id)
    if session is None:
        return None

    if transcript is not None:
        session.transcript = transcript
    session.status = status
    session.ended_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(session)
    return session


# Well beyond max_call_duration_seconds (600s) plus any plausible teardown /
# Connect-leg lifetime -- a row still "in_progress" this long after it started
# is not a live call, it is a row whose finalize path never completed.
_STUCK_CALL_SESSION_THRESHOLD_SECONDS = 30 * 60


async def reconcile_stuck_call_sessions(db: AsyncSession) -> int:
    """Safety net for the Calls tab's "every call is logged" guarantee.
    Several failure modes can leave a CallSession stranded at
    status="in_progress" with nothing ever finalizing it -- an exception
    thrown inside on_pipeline_finished itself (pipecat swallows event-handler
    exceptions), a crash in run_voice_pipeline's setup block after
    get_or_create_call_session committed but before _run_pipeline is called
    (its crash handler never runs), a cancellation where the finished handler
    doesn't complete. Each leaves a phantom "live" call on the host's Calls
    tab forever.

    This sweeps any such row older than _STUCK_CALL_SESSION_THRESHOLD_SECONDS
    and marks it status="failed" / call_type="MISSED_SYSTEM_FAILURE" so it
    reads correctly as a missed call. Returns the number of rows fixed.

    Deliberately skips rows with handoff_status="requested": a live
    Mira->host handoff intentionally stays "in_progress" so
    exotel_connect_routing can route the Connect leg (see on_pipeline_
    finished / webhooks/exotel.py). A handoff that genuinely never resolves
    is a real gap, but it needs the Phase 2 Connect-leg StatusCallback to
    close properly -- not a blunt timeout sweep that would race a
    legitimately long host conversation.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_STUCK_CALL_SESSION_THRESHOLD_SECONDS)
    stmt = select(CallSession).where(
        CallSession.status == "in_progress",
        CallSession.handoff_status.is_(None),
        # started_at is normally set, but fall back to created_at (a non-null
        # TimestampMixin column) so a row that somehow has a null started_at
        # is still swept rather than slipping through -- SQL `NULL < cutoff`
        # is NULL, which would silently exclude it.
        or_(
            CallSession.started_at < cutoff,
            and_(CallSession.started_at.is_(None), CallSession.created_at < cutoff),
        ),
    )
    stuck = list((await db.scalars(stmt)).all())
    if not stuck:
        return 0
    now = datetime.now(timezone.utc)
    for session in stuck:
        session.status = "failed"
        session.ended_at = session.ended_at or now
        session.call_type = "MISSED_SYSTEM_FAILURE"
        session.classification_confidence = 1.0
        session.classification_reason = (
            "Call never finalized (pipeline setup/teardown failure); reconciled by the stuck-session sweep."
        )
    await db.commit()
    logger.warning("reconcile_stuck_call_sessions fixed %d stranded in_progress row(s)", len(stuck))
    return len(stuck)


async def quality_event_analytics(db: AsyncSession, user_id: uuid.UUID, bucket: str = "week") -> dict:
    """Cross-call aggregate over CallQualityEvent (docs/tasks/
    building-intelligence.md, Implementation 3) -- read-only, mirrors
    faq_service.faq_gap_analytics's shape/bucketing exactly (most-frequent
    breakdown + a time trend), applied to guard/validator firings instead of
    FAQ gaps. No "resolve"/write action exists for quality events, same as
    faq_gap_analytics itself is read-only until a host acts via the separate
    POST /faq/gaps/{gap_id}/answer endpoint -- this task adds no equivalent
    action.
    """
    base_filters = [CallSession.user_id == user_id]

    most_frequent_stmt = (
        select(
            CallQualityEvent.rule,
            CallQualityEvent.severity,
            func.count().label("count"),
        )
        .join(CallSession, CallQualityEvent.call_session_id == CallSession.id)
        .where(*base_filters)
        .group_by(CallQualityEvent.rule, CallQualityEvent.severity)
        .order_by(func.count().desc())
        .limit(20)
    )
    most_frequent = [
        {"rule": row.rule, "severity": row.severity, "count": row.count}
        for row in (await db.execute(most_frequent_stmt)).all()
    ]

    day = func.date_trunc(bucket, func.timezone("UTC", CallQualityEvent.created_at))
    over_time_stmt = (
        select(day.label("bucket"), func.count().label("count"))
        .join(CallSession, CallQualityEvent.call_session_id == CallSession.id)
        .where(*base_filters)
        .group_by(day)
        .order_by(day)
    )
    over_time = [
        {"bucket": row.bucket.date().isoformat(), "count": row.count}
        for row in (await db.execute(over_time_stmt)).all()
    ]

    return {"most_frequent": most_frequent, "over_time": over_time}
