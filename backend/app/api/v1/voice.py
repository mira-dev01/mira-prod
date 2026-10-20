"""Voice entrypoints: the real Exotel call websocket, and an in-browser test
client (WebRTC) so the agent can be tried out from the dashboard without a
real phone call.

Configure the Exotel Voicebot Applet in the host's call flow with a static
URL: wss://<backend_base_url>/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>
(no extra HTTP round-trip endpoint needed -- Exotel connects directly).

The token is a PATH segment, not a `?token=` query param -- confirmed live
that Exotel's Voicebot Applet strips query strings from the configured WSS
URL before connecting (every real Exotel connection arrived at plain
`/exotel/ws` with no query string at all, while the same URL worked fine
tested directly with curl), so a query param can never reach us from Exotel
even though it's otherwise the more conventional place for a bearer token.

Also has a completely independent Twilio Voice entrypoint (added so
real-call testing can continue on Twilio's free trial when Exotel credits
run out, without touching any Exotel routing/pipeline code -- see
app/config.py's twilio_voice_webhook_token comment). Unlike Exotel, Twilio
needs a real HTTP webhook first (Twilio's "A call comes in" console
setting), which responds with TwiML telling Twilio where to open the media
stream:
  1. Set a Twilio number's Voice webhook to:
     https://<backend_base_url>/api/v1/voice/twilio/incoming/<TWILIO_VOICE_WEBHOOK_TOKEN>
  2. That returns TwiML pointing Twilio at:
     wss://<backend_base_url>/api/v1/voice/twilio/ws/<TWILIO_VOICE_WEBHOOK_TOKEN>
settings.backend_base_url must be the real public URL (not the localhost
default) for step 1's generated wss:// URL to actually be reachable.
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, status
from fastapi.responses import HTMLResponse, Response
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.integrations.exotel_client import verify_webhook_token
from app.integrations.twilio_voice import (
    build_connect_stream_twiml,
    build_reject_twiml,
    verify_voice_webhook_token,
)
from app.models.user import User
from app.services import faq_service
from app.voice.pipeline import (
    run_browser_lead_pipeline,
    run_browser_voice_pipeline,
    run_voice_pipeline,
    run_voice_pipeline_twilio,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/transcribe")
async def transcribe_dictation(
    audio: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """General-purpose "dictate into this field" transcription -- the mic
    button on dashboard text fields (see frontend's use-dictation.ts hook)
    records a clip and posts it here instead of typing. Reuses the same
    Sarvam batch transcription helper as the FAQ-gap voice-answer flow
    (app/api/v1/faq.py's /faq/gaps/{id}/answer-voice) and the pre-login
    voice-intro flow (app/api/v1/auth.py's /register-host/transcribe-intro)
    -- this is the same operation, just without either of those two flows'
    domain-specific follow-up (saving a FaqEntry / continuing registration),
    so it lives here as its own endpoint rather than bolting onto either.
    """
    text = await faq_service.transcribe_gap_answer_audio(audio)
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Could not transcribe audio -- please try again or type it instead"
        )
    return {"text": text}


def _ice_servers() -> list[IceServer]:
    # STUN alone only helps the two sides discover each other's public
    # address -- it doesn't relay media, so on hosts that don't allow a
    # direct UDP connection through (most cloud platforms, confirmed by ICE
    # timing out entirely on Render) it's not sufficient by itself. TURN
    # relays the actual audio through a third server over a connection that
    # looks like normal outbound traffic, which works around that. Optional:
    # falls back to STUN-only (fine on localhost) if not configured.
    servers = [
        IceServer(urls="stun:stun.l.google.com:19302"),
        IceServer(urls="stun:stun1.l.google.com:19302"),
    ]
    if settings.turn_url:
        if not (settings.turn_url.startswith("turn:") or settings.turn_url.startswith("turns:")):
            logger.error(
                "TURN_URL %r has an invalid scheme (must start with 'turn:' or 'turns:') — skipping",
                settings.turn_url,
            )
        else:
            servers.append(
                IceServer(urls=settings.turn_url, username=settings.turn_username, credential=settings.turn_credential)
            )
    # TURNS-over-TCP fallback for mobile carrier networks that block plain
    # UDP (see config.py's turn_url_tls comment) -- same credentials as
    # turn_url, offered as an additional candidate rather than a replacement.
    if settings.turn_url_tls:
        if not (settings.turn_url_tls.startswith("turn:") or settings.turn_url_tls.startswith("turns:")):
            logger.error(
                "TURN_URL_TLS %r has an invalid scheme (must start with 'turn:' or 'turns:') — skipping",
                settings.turn_url_tls,
            )
        else:
            servers.append(
                IceServer(
                    urls=settings.turn_url_tls, username=settings.turn_username, credential=settings.turn_credential
                )
            )
    return servers


@router.websocket("/exotel/ws/{token}")
async def exotel_voice_ws(websocket: WebSocket, token: str) -> None:
    if not verify_webhook_token(token):
        logger.warning("Rejected Exotel voice websocket with invalid/missing token")
        await websocket.close(code=4401)
        return

    await websocket.accept()
    _transport_type, call_data = await parse_telephony_websocket(websocket)
    await run_voice_pipeline(websocket, call_data)


@router.post("/twilio/incoming/{token}")
async def twilio_voice_incoming(request: Request, token: str) -> Response:
    """Twilio's "A call comes in" webhook -- see the module docstring for
    the two-step setup this requires (unlike Exotel's single static WSS
    URL). Twilio posts standard call params as form data; From/To are what
    we need to pass through as TwiML <Parameter> custom stream params,
    since Twilio's own WebSocket "start" event doesn't carry them (see
    app/integrations/twilio_voice.py's build_connect_stream_twiml
    docstring)."""
    if not verify_voice_webhook_token(token):
        logger.warning("Rejected Twilio voice webhook with invalid/missing token")
        return Response(content=build_reject_twiml(), media_type="application/xml", status_code=403)

    form = await request.form()
    from_number = str(form.get("From", ""))
    to_number = str(form.get("To", ""))

    ws_scheme = "wss" if settings.backend_base_url.startswith("https") else "ws"
    ws_host = settings.backend_base_url.split("://", 1)[-1].rstrip("/")
    stream_ws_url = f"{ws_scheme}://{ws_host}/api/v1/voice/twilio/ws/{token}"

    twiml = build_connect_stream_twiml(stream_ws_url, from_number, to_number)
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/twilio/ws/{token}")
async def twilio_voice_ws(websocket: WebSocket, token: str) -> None:
    if not verify_voice_webhook_token(token):
        logger.warning("Rejected Twilio voice websocket with invalid/missing token")
        await websocket.close(code=4401)
        return

    await websocket.accept()
    _transport_type, call_data = await parse_telephony_websocket(websocket)
    await run_voice_pipeline_twilio(websocket, call_data)


class BrowserOfferRequest(BaseModel):
    sdp: str
    type: str
    property_id: uuid.UUID | None = None


class BrowserOfferResponse(BaseModel):
    sdp: str
    type: str


@router.post("/test/offer", response_model=BrowserOfferResponse)
async def browser_test_offer(
    payload: BrowserOfferRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BrowserOfferResponse:
    """WebRTC signaling endpoint for the in-dashboard browser test client.

    Accepts an SDP offer, returns the SDP answer, and kicks off the same
    voice pipeline a real call would use, running in the background for the
    lifetime of the WebRTC connection. A property_id tests Guest Support for
    that property; omitting it tests the Lead Agent across the host's full
    portfolio.
    """
    connection = SmallWebRTCConnection(ice_servers=_ice_servers())
    await connection.initialize(sdp=payload.sdp, type=payload.type)
    answer = connection.get_answer()

    if payload.property_id is not None:
        property_ = await get_owned_property(db, payload.property_id, current_user)
        asyncio.create_task(run_browser_voice_pipeline(connection, property_))
    else:
        asyncio.create_task(run_browser_lead_pipeline(connection, current_user))

    return BrowserOfferResponse(sdp=answer["sdp"], type=answer["type"])


@router.get("/test", response_class=HTMLResponse)
async def browser_test_page(token: str, property_id: str = "") -> str:
    """Minimal mic-in/speaker-out WebRTC test page. Opened from the
    dashboard with the host's JWT and, optionally, a property id (omit to
    test the Lead Agent across the host's full portfolio instead)."""
    # Reuses _ice_servers() (not a separate hand-rolled list) so this page's
    # ICE config can never drift out of sync with what the /test/offer
    # endpoint actually offers.
    ice_servers: list[dict] = [
        {"urls": server.urls, "username": server.username or "", "credential": server.credential or ""}
        for server in _ice_servers()
    ]

    return (
        _TEST_PAGE_TEMPLATE
        .replace("__PROPERTY_ID__", property_id)
        .replace("__TOKEN__", token)
        .replace("__ICE_SERVERS__", json.dumps(ice_servers))
    )


_TEST_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MIRA voice test</title>
  <style>
    :root {
      --bg: #0f0e0c;
      --fg: #f0ebe3;
      --muted: #8a7e72;
      --coral: #d94f3d;
      --gold: #c9a882;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--fg);
      font-family: -apple-system, "DM Sans", system-ui, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      padding: 32px 24px 56px;
    }
    .chip {
      font-size: 13px;
      color: var(--muted);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 999px;
      padding: 6px 16px;
    }
    .stage {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 28px;
      position: relative;
    }
    .halo {
      position: absolute;
      width: 520px;
      height: 520px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(217, 79, 61, 0.16) 0%, rgba(217, 79, 61, 0) 70%);
      transition: opacity 0.4s ease;
      opacity: 0.3;
      filter: blur(4px);
    }
    .orb-wrap {
      position: relative;
      width: 220px;
      height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .ring {
      position: absolute;
      border-radius: 50%;
      border: 1px solid rgba(240, 235, 227, 0.14);
      transition: transform 0.08s linear, opacity 0.08s linear, border-color 0.3s ease;
    }
    .ring1 { width: 100%; height: 100%; }
    .ring2 { width: 78%; height: 78%; }
    .ring3 { width: 56%; height: 56%; }
    .orb {
      position: relative;
      width: 120px;
      height: 120px;
      border-radius: 50%;
      background: radial-gradient(circle at 32% 30%, rgba(240, 235, 227, 0.9), var(--gold) 45%, var(--coral) 100%);
      box-shadow: 0 0 40px 8px rgba(217, 79, 61, 0.35);
      transition: box-shadow 0.08s linear, transform 0.08s linear, filter 0.3s ease;
      filter: saturate(0.85);
    }
    .caption {
      font-size: 15px;
      color: var(--muted);
      min-height: 20px;
      letter-spacing: 0.01em;
    }
    .controls { display: flex; flex-direction: column; align-items: center; gap: 14px; }
    .mic-btn {
      width: 64px;
      height: 64px;
      border-radius: 50%;
      border: none;
      cursor: pointer;
      background: var(--fg);
      color: var(--bg);
      font-size: 22px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s ease, transform 0.1s ease;
    }
    .mic-btn:active { transform: scale(0.94); }
    .mic-btn.live { background: var(--coral); color: var(--fg); }
    .mic-btn:disabled { opacity: 0.5; cursor: default; }
    .hint { font-size: 13px; color: var(--muted); }
    audio { display: none; }
  </style>
</head>
<body>
  <div class="chip" id="modeChip">MIRA &middot; Live test</div>

  <div class="stage">
    <div class="halo" id="halo"></div>
    <div class="orb-wrap">
      <div class="ring ring1" id="ring1"></div>
      <div class="ring ring2" id="ring2"></div>
      <div class="ring ring3" id="ring3"></div>
      <div class="orb" id="orb"></div>
    </div>
    <div class="caption" id="caption">Tap the mic to connect</div>
  </div>

  <div class="controls">
    <button class="mic-btn" id="connectBtn" aria-label="Connect">&#127908;</button>
    <div class="hint" id="hint">Allow microphone access, then start speaking</div>
  </div>

  <audio id="remoteAudio" autoplay></audio>

  <script>
    const propertyId = "__PROPERTY_ID__";
    const token = "__TOKEN__";
    if (!propertyId) {
      document.getElementById("modeChip").textContent = "MIRA \\u00b7 Lead Agent test";
      document.getElementById("hint").textContent =
        "Testing the Lead Agent across your full portfolio. Allow microphone access, then start speaking.";
    }

    const captionEl = document.getElementById("caption");
    const hintEl = document.getElementById("hint");
    const connectBtn = document.getElementById("connectBtn");
    const orb = document.getElementById("orb");
    const halo = document.getElementById("halo");
    const rings = [document.getElementById("ring1"), document.getElementById("ring2"), document.getElementById("ring3")];

    let pc = null;
    let audioCtx = null;
    let rafId = null;
    let localAnalyser = null;
    let remoteAnalyser = null;

    function amplitudeOf(analyser) {
      if (!analyser) return 0;
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i++) {
        const centered = (data[i] - 128) / 128;
        sumSquares += centered * centered;
      }
      return Math.sqrt(sumSquares / data.length); // 0..~1 RMS
    }

    function animate() {
      const userLevel = amplitudeOf(localAnalyser);
      const agentLevel = amplitudeOf(remoteAnalyser);

      const speaking = agentLevel > 0.04 && agentLevel > userLevel;
      const listening = !speaking && userLevel > 0.04;
      const level = speaking ? agentLevel : listening ? userLevel : 0;

      const tint = speaking ? "var(--coral)" : "var(--gold)";
      const scale = 1 + Math.min(level, 1) * 0.35;
      const glow = 10 + Math.min(level, 1) * 70;

      orb.style.transform = "scale(" + scale.toFixed(3) + ")";
      orb.style.boxShadow = "0 0 " + glow.toFixed(0) + "px " + (8 + level * 14).toFixed(0) + "px " +
        (speaking ? "rgba(217, 79, 61, 0.55)" : "rgba(201, 168, 130, 0.5)");
      halo.style.opacity = (0.25 + Math.min(level, 1) * 0.4).toFixed(2);

      rings.forEach((ring, i) => {
        const ringScale = 1 + Math.min(level, 1) * (0.08 * (i + 1));
        ring.style.transform = "scale(" + ringScale.toFixed(3) + ")";
        ring.style.borderColor = speaking ? "rgba(217, 79, 61, 0.35)" : "rgba(201, 168, 130, 0.3)";
        ring.style.opacity = level > 0.02 ? "1" : "0.5";
      });

      if (pc && pc.connectionState === "connected") {
        captionEl.textContent = speaking ? "Mira is speaking..." : listening ? "Listening..." : "Connected -- say hello";
      }

      rafId = requestAnimationFrame(animate);
    }

    // A plain .onclick assignment (not addEventListener) so that switching
    // to the "end call" handler below cleanly REPLACES this one instead of
    // stacking alongside it. They were previously registered through two
    // different handler slots (addEventListener here, .onclick for end-call)
    // -- clicking the button to end a call fired BOTH: the end-call logic
    // AND this connect handler still listening underneath it, which
    // silently started a brand new call the instant the old one closed.
    async function startCall() {
      connectBtn.disabled = true;
      captionEl.textContent = "Requesting microphone...";

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();

      localAnalyser = audioCtx.createAnalyser();
      localAnalyser.fftSize = 256;
      audioCtx.createMediaStreamSource(stream).connect(localAnalyser);

      pc = new RTCPeerConnection({ iceServers: __ICE_SERVERS__ });
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));

      pc.ontrack = (event) => {
        document.getElementById("remoteAudio").srcObject = event.streams[0];
        remoteAnalyser = audioCtx.createAnalyser();
        remoteAnalyser.fftSize = 256;
        audioCtx.createMediaStreamSource(event.streams[0]).connect(remoteAnalyser);
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          connectBtn.textContent = "\\u2715";
          connectBtn.classList.add("live");
          connectBtn.disabled = false;
          hintEl.textContent = "Tap to end the test call";
          connectBtn.onclick = () => {
            pc.close();
            cancelAnimationFrame(rafId);
            captionEl.textContent = "Call ended";
            hintEl.textContent = "Refresh the page to test again";
            connectBtn.disabled = true;
          };
        } else if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
          captionEl.textContent = "Connection " + pc.connectionState;
        }
      };

      captionEl.textContent = "Creating offer...";
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      // Wait for ICE gathering (including TURN relay candidate allocation)
      // before sending the offer. Cap at 5 s: if gathering hasn't completed
      // by then we send whatever candidates exist -- the relay candidate
      // appears in < 200 ms normally, so hitting the cap means something is
      // genuinely wrong and waiting longer won't help.
      captionEl.textContent = "Gathering candidates...";
      await new Promise((resolve) => {
        if (pc.iceGatheringState === "complete") { resolve(); return; }
        const t = setTimeout(resolve, 5000);
        pc.onicegatheringstatechange = () => {
          if (pc.iceGatheringState === "complete") { clearTimeout(t); resolve(); }
        };
      });

      captionEl.textContent = "Connecting to MIRA...";
      const res = await fetch(window.location.origin + "/api/v1/voice/test/offer", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + token,
        },
        body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type, property_id: propertyId || null }),
      });

      if (!res.ok) {
        captionEl.textContent = "Failed to connect: " + res.status;
        connectBtn.disabled = false;
        return;
      }

      const answer = await res.json();
      await pc.setRemoteDescription(answer);
      animate();
    }

    connectBtn.onclick = startCall;
  </script>
</body>
</html>
"""
