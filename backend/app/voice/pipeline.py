"""Builds and runs one Pipecat voice pipeline per call.

Two entry points share the same pipeline-building logic (`_run_pipeline`):
- run_voice_pipeline: real Exotel calls, over a WebSocket carrying Exotel's
  raw-PCM media protocol (see app/api/v1/voice.py).
- run_browser_voice_pipeline: the in-dashboard "test in browser" feature,
  over WebRTC, for testing without a real phone call.

Both feed into Sarvam STT -> Groq/Anthropic LLM (function-calling into
app/voice/tools.py, which wraps the unchanged business logic in
app/services/tool_handlers.py) -> Sarvam TTS.
"""

import logging
import uuid

import aiohttp
from fastapi import WebSocket
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.runner.types import CallData
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.property import Property
from app.models.user import User
from app.prompts.system_prompt import (
    build_lead_system_prompt,
    build_system_prompt,
    first_message_for,
    lead_first_message_for,
)
from app.services import call_service, lead_service
from app.voice.tools import build_voice_tools

logger = logging.getLogger(__name__)


def _build_llm():
    if settings.llm_provider == "anthropic":
        return AnthropicLLMService(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    return GroqLLMService(api_key=settings.groq_api_key, model=settings.groq_model)


async def _run_pipeline(
    transport: BaseTransport,
    property_id: uuid.UUID | None,
    call_session_id: uuid.UUID,
    host_user_id: uuid.UUID,
    system_prompt: str,
    first_message: str,
    caller_number: str | None = None,
    property_name: str | None = None,
) -> None:
    # Every call gets a CRM lead record up front -- don't rely on the LLM
    # remembering to call update_lead. It enriches this same row later
    # (matched by call_session_id); this just guarantees one exists at all,
    # even for calls that get escalated/resolved without the LLM ever
    # touching the tool.
    async with AsyncSessionLocal() as lead_db:
        await lead_service.upsert_lead(
            lead_db,
            host_user_id,
            call_session_id,
            phone=caller_number or None,
            properties_discussed=[property_name] if property_name else None,
        )

    stt = SarvamSTTService(
        api_key=settings.sarvam_api_key,
        model=settings.sarvam_stt_model,
        mode="codemix",  # transcribe Hindi/English/Hinglish as spoken, no translation
    )

    async with aiohttp.ClientSession() as http_session:
        tts = SarvamTTSService(
            api_key=settings.sarvam_api_key,
            aiohttp_session=http_session,
            model=settings.sarvam_tts_model,
            voice_id=settings.sarvam_tts_speaker,
        )
        llm = _build_llm()

        tools = build_voice_tools(call_session_id, property_id, host_user_id)
        context = LLMContext(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "assistant", "content": first_message},
            ],
            tools=tools,
        )
        # Pipecat defaults end-of-turn detection to a local ONNX transformer
        # model (LocalSmartTurnAnalyzerV3) running CPU inference on every
        # utterance. On a dev box also running the frontend, backend, and a
        # browser tab encoding/decoding WebRTC, that competes for CPU with
        # the real-time audio loop and is a real source of crackle/latency.
        # SpeechTimeoutUserTurnStopStrategy is VAD + timer based instead --
        # no local ML inference -- and its stt_timeout safety net is
        # designed for exactly an STT-based pipeline like ours.
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)]
                )
            ),
        )

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                user_aggregator,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )

        worker = PipelineWorker(pipeline, params=PipelineParams())

        @worker.event_handler("on_pipeline_finished")
        async def _on_finished(worker, frame):
            transcript = "\n".join(
                f"{message.get('role')}: {message.get('content')}"
                for message in context.messages
                if message.get("role") in ("user", "assistant")
            )
            async with AsyncSessionLocal() as finalize_db:
                await call_service.finalize_call_session(finalize_db, call_session_id, transcript, None)

        runner = WorkerRunner()
        await runner.add_workers(worker)
        await runner.run()


async def run_voice_pipeline(websocket: WebSocket, call_data: CallData) -> None:
    exotel_call_id = call_data.call_id
    dialed_number = call_data.to_number
    caller_number = call_data.from_number

    async with AsyncSessionLocal() as db:
        property_ = await call_service.get_property_by_number(db, dialed_number)
        lead_user = None if property_ is not None else await call_service.get_user_by_lead_number(db, dialed_number)

        if property_ is None and lead_user is None:
            logger.warning(
                "No property or lead line configured for dialed number %s, ending call %s",
                dialed_number,
                exotel_call_id,
            )
            try:
                await websocket.close()
            except RuntimeError:
                pass  # caller already disconnected
            return

        guest = await call_service.get_or_create_guest_profile(db, caller_number)

        if property_ is not None:
            session = await call_service.get_or_create_call_session(
                db,
                exotel_call_id=exotel_call_id,
                property_id=property_.id,
                guest_profile_id=guest.id if guest else None,
                caller_number=caller_number,
                user_id=property_.user_id,
            )
            system_prompt = build_system_prompt(property_, guest)
            first_message = first_message_for(property_, guest)
            property_id = property_.id
            property_name = property_.name
            host_user_id = property_.user_id
        else:
            properties = list(
                (await db.scalars(select(Property).where(Property.user_id == lead_user.id))).all()
            )
            session = await call_service.get_or_create_call_session(
                db,
                exotel_call_id=exotel_call_id,
                property_id=None,
                guest_profile_id=guest.id if guest else None,
                caller_number=caller_number,
                user_id=lead_user.id,
            )
            system_prompt = build_lead_system_prompt(lead_user, properties)
            first_message = lead_first_message_for(lead_user)
            property_id = None
            property_name = None
            host_user_id = lead_user.id

        call_session_id = session.id

    serializer = ExotelFrameSerializer(stream_sid=call_data.stream_id, call_sid=exotel_call_id)
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    await _run_pipeline(
        transport,
        property_id,
        call_session_id,
        host_user_id,
        system_prompt,
        first_message,
        caller_number=caller_number,
        property_name=property_name,
    )


async def run_browser_voice_pipeline(connection: SmallWebRTCConnection, property_: Property) -> None:
    """Same pipeline as a real call, but over WebRTC from the dashboard's
    "test in browser" page instead of an Exotel phone call. There's no real
    caller phone number, so we use a fixed placeholder identity
    (BROWSER_TEST_CALLER_NUMBER) for both the guest profile and the call
    session, instead of leaving them unset -- the frontend renders that
    value as a "Browser test" label rather than hiding the row entirely."""
    async with AsyncSessionLocal() as db:
        guest = await call_service.get_or_create_guest_profile(
            db, call_service.BROWSER_TEST_CALLER_NUMBER, name="Browser test guest"
        )
        session = await call_service.get_or_create_call_session(
            db,
            exotel_call_id=None,
            property_id=property_.id,
            guest_profile_id=guest.id if guest else None,
            caller_number=call_service.BROWSER_TEST_CALLER_NUMBER,
            user_id=property_.user_id,
        )
        system_prompt = build_system_prompt(property_, None)
        first_message = first_message_for(property_, None)
        property_id = property_.id
        host_user_id = property_.user_id
        call_session_id = session.id

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    await _run_pipeline(
        transport, property_id, call_session_id, host_user_id, system_prompt, first_message, property_name=property_.name
    )


async def run_browser_lead_pipeline(connection: SmallWebRTCConnection, user: User) -> None:
    """Same as run_browser_voice_pipeline, but for testing the Lead Agent
    flow across a host's full portfolio instead of one property."""
    async with AsyncSessionLocal() as db:
        properties = list((await db.scalars(select(Property).where(Property.user_id == user.id))).all())
        guest = await call_service.get_or_create_guest_profile(
            db, call_service.BROWSER_TEST_CALLER_NUMBER, name="Browser test guest"
        )
        session = await call_service.get_or_create_call_session(
            db,
            exotel_call_id=None,
            property_id=None,
            guest_profile_id=guest.id if guest else None,
            caller_number=call_service.BROWSER_TEST_CALLER_NUMBER,
            user_id=user.id,
        )
        system_prompt = build_lead_system_prompt(user, properties)
        first_message = lead_first_message_for(user)
        call_session_id = session.id

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    await _run_pipeline(transport, None, call_session_id, user.id, system_prompt, first_message)
