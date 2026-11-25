"""The marketing site's public "test the agent" call (the landing page's floating pin).

Three unauthenticated routes, off (404) unless DEMO_HOST_EMAIL and DEMO_CALL_SECRET are set:

  GET  /public/demo-call/config   whether the pin should show, the call length, the Turnstile site key
  POST /public/demo-call/session  name + number (+ bot check) -> a signed single-use call token
  POST /public/demo-call/offer    WebRTC offer + that token -> the answer, and the call starts

The call is the Lead Agent for the sample host only, with every side effect simulated (app/voice/demo_mode.py), hard
capped in length, and bounded by the limits in app/services/demo_call_service.py. Nothing here can reach a real
host's data: the host is looked up by DEMO_HOST_EMAIL, never from the request.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pydantic import BaseModel
from sqlalchemy import select

from app.api.v1.voice import ice_servers_payload, _ice_servers
from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import email_client
from app.models.demo_call import DemoCallRequest
from app.models.user import User
from app.services import demo_call_service as demo
from app.utils import background
from app.voice.pipeline import run_browser_demo_pipeline

router = APIRouter(prefix="/public/demo-call", tags=["demo-call"])

_NO_STORE = {"Cache-Control": "no-store"}


def _require_enabled() -> None:
    if not settings.demo_call_enabled:
        raise HTTPException(404, "Not found")


class DemoConfig(BaseModel):
    enabled: bool
    max_seconds: int
    turnstile_site_key: str | None = None


@router.get("/config", response_model=DemoConfig)
async def demo_config() -> DemoConfig:
    if not settings.demo_call_enabled:
        return DemoConfig(enabled=False, max_seconds=0)
    return DemoConfig(
        enabled=True,
        max_seconds=settings.demo_call_max_seconds,
        turnstile_site_key=settings.demo_turnstile_site_key or None,
    )


class DemoSessionRequest(BaseModel):
    name: str
    phone: str
    turnstile_token: str | None = None


class DemoSessionResponse(BaseModel):
    token: str
    max_seconds: int
    ice_servers: list[dict]


@router.post("/session", response_model=DemoSessionResponse)
async def demo_session(payload: DemoSessionRequest, request: Request) -> DemoSessionResponse:
    _require_enabled()
    name = demo.clean_name(payload.name)
    if name is None:
        raise HTTPException(400, "Please enter your name.")
    phone = demo.clean_phone(payload.phone)
    if phone is None:
        raise HTTPException(400, "Please enter a valid phone number, with the country code if it is not an Indian number.")

    ip = demo.client_ip(request)
    if not await demo.verify_turnstile(payload.turnstile_token, ip):
        raise HTTPException(400, "We could not confirm you are not a bot. Please try again.")

    ip_hash = demo.hash_ip(ip)
    async with AsyncSessionLocal() as db:
        try:
            await demo.enforce_limits(db, ip_hash=ip_hash, phone=phone)
        except demo.DemoCallRefused as refusal:
            raise HTTPException(refusal.status_code, refusal.message) from refusal
        host = await db.scalar(select(User).where(User.email == settings.demo_host_email.strip()))
        if host is None:
            logger.error("DEMO_HOST_EMAIL %r matches no user: the demo call is configured but not seeded", settings.demo_host_email)
            raise HTTPException(503, "The live test is not available right now. Please book a demo instead.")
        row = DemoCallRequest(name=name, phone=phone, ip_hash=ip_hash)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        request_id = row.id

    if settings.demo_lead_notify_email.strip():
        background.spawn(_notify_lead(name, phone))

    return DemoSessionResponse(
        token=demo.issue_token(request_id),
        max_seconds=settings.demo_call_max_seconds,
        ice_servers=ice_servers_payload(),
    )


async def _notify_lead(name: str, phone: str) -> None:
    try:
        await email_client.send_email(
            settings.demo_lead_notify_email.strip(),
            "A visitor is testing Mira on the website",
            f"{name} ({phone}) started the live test call on the website.",
            html_body=f"<p><b>{_escape(name)}</b> ({_escape(phone)}) started the live test call on the website.</p>",
        )
    except Exception:
        logger.exception("Demo call lead notification failed")


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class DemoOfferRequest(BaseModel):
    sdp: str
    type: str


class DemoOfferResponse(BaseModel):
    sdp: str
    type: str


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@router.post("/offer", response_model=DemoOfferResponse)
async def demo_offer(payload: DemoOfferRequest, authorization: str | None = Header(default=None)) -> DemoOfferResponse:
    _require_enabled()
    request_id = demo.verify_token(_bearer(authorization))
    if request_id is None:
        raise HTTPException(401, "This call link has expired. Please start again.")

    async with AsyncSessionLocal() as db:
        row = await db.get(DemoCallRequest, request_id)
        if row is None:
            raise HTTPException(401, "This call link has expired. Please start again.")
        if row.started_at is not None:
            raise HTTPException(409, "This call has already been used. Please start again.")
        host = await db.scalar(select(User).where(User.email == settings.demo_host_email.strip()))
        if host is None:
            raise HTTPException(503, "The live test is not available right now. Please book a demo instead.")
        if not demo.active_calls.try_acquire(request_id):
            raise HTTPException(503, "All test lines are busy. Please try again in a minute.")
        row.started_at = datetime.now(timezone.utc)
        await db.commit()

    async def finished() -> None:
        demo.active_calls.release(request_id)
        async with AsyncSessionLocal() as db:
            record = await db.get(DemoCallRequest, request_id)
            if record is not None and record.started_at is not None:
                now = datetime.now(timezone.utc)
                record.ended_at = now
                record.duration_seconds = max(0, int((now - record.started_at).total_seconds()))
                await db.commit()

    try:
        connection = SmallWebRTCConnection(ice_servers=_ice_servers())
        await connection.initialize(sdp=payload.sdp, type=payload.type)
        answer = connection.get_answer()
    except Exception:
        logger.exception("Demo call: could not set up the WebRTC connection")
        await finished()
        raise HTTPException(400, "We could not connect your microphone. Please try again.") from None

    background.spawn(
        run_browser_demo_pipeline(connection, host, max_seconds=settings.demo_call_max_seconds, on_finished=finished)
    )
    return DemoOfferResponse(sdp=answer["sdp"], type=answer["type"])
