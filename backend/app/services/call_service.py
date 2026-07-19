import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.property import Property
from app.models.user import User
from app.schemas.call_classification import ClassificationResult

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
    ai_summary: str | None,
    status: str = "completed",
) -> CallSession | None:
    session = await db.get(CallSession, call_session_id)
    if session is None:
        return None

    if transcript is not None:
        session.transcript = transcript
    if ai_summary is not None:
        session.ai_summary = ai_summary
    session.status = status
    session.ended_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(session)
    return session
