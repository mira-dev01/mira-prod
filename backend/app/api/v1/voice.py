"""Voice entrypoints: the real Exotel call websocket, and an in-browser test
client (WebRTC) so the agent can be tried out from the dashboard without a
real phone call.

Configure the Exotel Voicebot Applet in the host's call flow with a static
URL: wss://<backend_base_url>/api/v1/voice/exotel/ws?token=<EXOTEL_WEBHOOK_TOKEN>
(no extra HTTP round-trip endpoint needed -- Exotel connects directly).
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket
from fastapi.responses import HTMLResponse
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import get_owned_property
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.integrations.exotel_client import verify_webhook_token
from app.models.user import User
from app.voice.pipeline import run_browser_voice_pipeline, run_voice_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])


@router.websocket("/exotel/ws")
async def exotel_voice_ws(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
    if not verify_webhook_token(token):
        logger.warning("Rejected Exotel voice websocket with invalid/missing token")
        await websocket.close(code=4401)
        return

    await websocket.accept()
    _transport_type, call_data = await parse_telephony_websocket(websocket)
    await run_voice_pipeline(websocket, call_data)


class BrowserOfferRequest(BaseModel):
    sdp: str
    type: str
    property_id: uuid.UUID


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
    lifetime of the WebRTC connection.
    """
    property_ = await get_owned_property(db, payload.property_id, current_user)

    connection = SmallWebRTCConnection()
    await connection.initialize(sdp=payload.sdp, type=payload.type)
    answer = connection.get_answer()

    asyncio.create_task(run_browser_voice_pipeline(connection, property_))

    return BrowserOfferResponse(sdp=answer["sdp"], type=answer["type"])


@router.get("/test", response_class=HTMLResponse)
async def browser_test_page(property_id: str, token: str) -> str:
    """Minimal mic-in/speaker-out WebRTC test page. Opened from the
    dashboard's Properties page with the host's JWT and a property id."""
    return _TEST_PAGE_TEMPLATE.replace("__PROPERTY_ID__", property_id).replace("__TOKEN__", token)


_TEST_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MIRA voice test</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 480px; margin: 60px auto; text-align: center; }
    button { font-size: 16px; padding: 10px 24px; border-radius: 8px; cursor: pointer; }
    #status { margin-top: 16px; color: #555; }
  </style>
</head>
<body>
  <h1>Talk to MIRA</h1>
  <p>Allow microphone access, then click connect and start speaking.</p>
  <button id="connectBtn">Connect</button>
  <div id="status">Idle</div>
  <audio id="remoteAudio" autoplay></audio>
  <script>
    const propertyId = "__PROPERTY_ID__";
    const token = "__TOKEN__";
    const statusEl = document.getElementById("status");
    const connectBtn = document.getElementById("connectBtn");

    connectBtn.addEventListener("click", async () => {
      connectBtn.disabled = true;
      statusEl.textContent = "Requesting microphone...";

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const pc = new RTCPeerConnection();
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));

      pc.ontrack = (event) => {
        document.getElementById("remoteAudio").srcObject = event.streams[0];
      };

      pc.onconnectionstatechange = () => {
        statusEl.textContent = "Connection: " + pc.connectionState;
      };

      statusEl.textContent = "Creating offer...";
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      statusEl.textContent = "Connecting to MIRA...";
      const res = await fetch(window.location.origin + "/api/v1/voice/test/offer", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + token,
        },
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type, property_id: propertyId }),
      });

      if (!res.ok) {
        statusEl.textContent = "Failed to connect: " + res.status;
        connectBtn.disabled = false;
        return;
      }

      const answer = await res.json();
      await pc.setRemoteDescription(answer);
      statusEl.textContent = "Connected -- say hello!";
    });
  </script>
</body>
</html>
"""
