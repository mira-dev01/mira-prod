import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.property import Property
from app.prompts.system_prompt import build_system_prompt, first_message_for
from app.prompts.tool_definitions import ALL_TOOLS


async def get_property_by_number(db: AsyncSession, dialed_number: str | None) -> Property | None:
    if not dialed_number:
        return None
    return await db.scalar(select(Property).where(Property.exophone == dialed_number))


async def resolve_property_for_call(db: AsyncSession, call: dict) -> Property | None:
    """Vapi's `assistant-request` payload identifies the dialed number either by a
    `phoneNumberId` (the resource id returned when we registered the BYO number --
    preferred, exact match) or a `phoneNumber.number` string (fallback)."""
    phone_number_id = call.get("phoneNumberId")
    if phone_number_id:
        property_ = await db.scalar(select(Property).where(Property.vapi_phone_number_id == phone_number_id))
        if property_ is not None:
            return property_

    dialed_number = (call.get("phoneNumber") or {}).get("number")
    return await get_property_by_number(db, dialed_number)


def extract_caller_number(call: dict) -> str | None:
    return (call.get("customer") or {}).get("number")


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


def build_transient_assistant_config(property_: Property, guest: GuestProfile | None) -> dict:
    """Per-call assistant config returned to Vapi's `assistant-request` webhook.

    transcriber=Deepgram, model=Groq, voice=ElevenLabs -- the providers the
    host already has accounts with -- routed through Vapi's BYO-provider-key
    credentials (see scripts/setup_vapi_assistant.py).
    """
    server = {"url": f"{settings.backend_base_url}/api/v1/webhooks/vapi", "secret": settings.vapi_webhook_secret}

    return {
        "name": f"{settings.vapi_assistant_name} - {property_.name}",
        "firstMessage": first_message_for(property_, guest),
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "en"},
        "model": {
            "provider": "groq",
            "model": settings.groq_model,
            "messages": [{"role": "system", "content": build_system_prompt(property_, guest)}],
            "tools": ALL_TOOLS,
        },
        "voice": {"provider": "11labs", "voiceId": settings.elevenlabs_voice_id or "default"},
        "server": server,
    }


async def get_or_create_call_session(
    db: AsyncSession,
    vapi_call_id: str | None,
    property_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None,
    caller_number: str | None,
    exotel_call_id: str | None = None,
) -> CallSession:
    session = None
    if vapi_call_id:
        session = await db.scalar(select(CallSession).where(CallSession.vapi_call_id == vapi_call_id))

    if session is not None:
        return session

    session = CallSession(
        vapi_call_id=vapi_call_id,
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
    """Exotel and Vapi are bridged directly over a SIP trunk (see README), so
    there is no shared call id between the two systems. We opportunistically
    merge an Exotel status callback into a Vapi-created session for the same
    caller if one started in roughly the last 2 minutes; otherwise this
    becomes the call_sessions record on its own (e.g. for calls that never
    reached an AI turn -- busy/failed/no-answer)."""
    session = await db.scalar(select(CallSession).where(CallSession.exotel_call_id == exotel_call_id))

    if session is None and caller_number:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
        session = await db.scalar(
            select(CallSession)
            .where(
                CallSession.caller_number == caller_number,
                CallSession.exotel_call_id.is_(None),
                CallSession.created_at >= cutoff,
            )
            .order_by(CallSession.created_at.desc())
        )

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
    else:
        session.exotel_call_id = exotel_call_id

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
    vapi_call_id: str,
    transcript: str | None,
    ai_summary: str | None,
    status: str = "completed",
) -> CallSession | None:
    session = await db.scalar(select(CallSession).where(CallSession.vapi_call_id == vapi_call_id))
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
