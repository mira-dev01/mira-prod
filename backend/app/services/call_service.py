import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.property import Property


async def get_property_by_number(db: AsyncSession, dialed_number: str | None) -> Property | None:
    if not dialed_number:
        return None
    return await db.scalar(select(Property).where(Property.exophone == dialed_number))


def extract_caller_number(call: dict) -> str | None:
    return call.get("from") or None


async def get_or_create_guest_profile(db: AsyncSession, caller_number: str | None) -> GuestProfile | None:
    if not caller_number:
        return None

    guest = await db.scalar(select(GuestProfile).where(GuestProfile.phone == caller_number))
    if guest is not None:
        return guest

    guest = GuestProfile(phone=caller_number, total_stays=0)
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
) -> CallSession:
    session = None
    if exotel_call_id:
        session = await db.scalar(select(CallSession).where(CallSession.exotel_call_id == exotel_call_id))

    if session is not None:
        return session

    session = CallSession(
        exotel_call_id=exotel_call_id,
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
