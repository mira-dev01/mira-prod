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
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.runner.types import CallData
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService
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
        return AnthropicLLMService(
            api_key=settings.anthropic_api_key,
            settings=AnthropicLLMService.Settings(model=settings.anthropic_model),
        )
    if settings.llm_provider == "openrouter":
        # OpenRouter is OpenAI-compatible -- same trick GroqLLMService itself
        # uses (OpenAILLMService pointed at a different base_url). Swapping
        # models (GPT-4.1, Claude, Llama, ...) is just changing
        # openrouter_model, no new integration code per model.
        #
        # max_completion_tokens is capped explicitly because OpenRouter's
        # free-tier credit check rejects a request based on the *requested*
        # ceiling (defaults to 65536, unbounded), not actual usage. 500 was
        # the first value tried (when the account had ~$0 balance) and
        # turned out too tight -- real replies were getting cut off
        # mid-sentence. 900 still safely excludes runaway-length output on a
        # phone call, with real margin over what a property-recommendation
        # or pricing-breakdown reply actually needs.
        #
        # reasoning_effort is a property of gpt-oss itself (defaults to
        # "medium", a hidden chain-of-thought pass that adds latency), not
        # something specific to Groq's hosting of it -- carry the same fix
        # over when this model is reached via OpenRouter instead.
        extra = {"reasoning_effort": "low"} if "gpt-oss" in settings.openrouter_model else {}
        return OpenAILLMService(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            settings=OpenAILLMService.Settings(
                model=settings.openrouter_model, max_completion_tokens=900, extra=extra
            ),
        )
    # openai/gpt-oss-120b defaults to "medium" reasoning effort on Groq, which
    # adds a hidden chain-of-thought pass before every reply -- a real source
    # of multi-second latency on a phone call. "low" trades reasoning depth
    # for speed, which is the right tradeoff for live conversational replies.
    return GroqLLMService(
        api_key=settings.groq_api_key,
        settings=GroqLLMService.Settings(model=settings.groq_model, extra={"reasoning_effort": "low"}),
    )


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
        #
        # user_speech_timeout was 0.6s -- real testing showed the agent
        # jumping in mid-sentence during normal mid-thought pauses (more
        # noticeable on faster-responding models, since there's less cover
        # for a premature trigger). 0.9s trades a little latency for fewer
        # false interruptions.
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

        # enable_metrics: per-stage time-to-first-byte (logged as
        # "<stage> TTFB: N.NNNs"). enable_usage_metrics: exact prompt/
        # completion token counts per LLM call (logged as "<stage> prompt
        # tokens: X, completion tokens: Y") -- this is how we compute real
        # $ cost per call per provider, not an estimate.
        worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))

        @worker.event_handler("on_pipeline_finished")
        async def _on_finished(worker, frame):
            transcript = "\n".join(
                f"{message.get('role')}: {message.get('content')}"
                for message in context.messages
                if message.get("role") in ("user", "assistant")
            )
            async with AsyncSessionLocal() as finalize_db:
                await call_service.finalize_call_session(finalize_db, call_session_id, transcript, None)
                # A call that ended with no user turn at all (e.g. a
                # reconnect blip right after a real call, or a mic
                # permission failure) leaves behind the empty Lead row every
                # call gets up front -- clean it up so it doesn't look like
                # a duplicate/phantom entry on the Leads page.
                if not any(m.get("role") == "user" for m in context.messages):
                    await lead_service.delete_if_empty(finalize_db, call_session_id)

        # TTSSpeakFrame bypasses STT and LLM entirely — goes directly to the
        # TTS service, which synthesizes and plays it immediately. The context
        # already has the first_message as a pre-seeded assistant turn, so
        # the LLM knows what was said without us needing to track it here.
        @transport.event_handler("on_client_connected")
        async def _on_connected_greeting(transport, client):
            try:
                await llm.push_frame(TTSSpeakFrame(first_message))
            except Exception:
                logger.warning("Initial greeting could not be sent; pipeline continues normally.")

        # The transport firing on_client_disconnected does NOT by itself
        # drive the pipeline to a terminal state -- it's just a callback.
        # Without this, a call that ends by the browser tab closing (the
        # normal way a test call ends, as opposed to clicking the in-page
        # disconnect button) never reaches on_pipeline_finished, so the
        # transcript never saves and the call sits at status="in_progress"
        # forever. This is pipecat's own documented pattern for exactly
        # this case (see its CLI template's event_handlers.jinja2).
        @transport.event_handler("on_client_disconnected")
        async def _on_client_disconnected(transport, client):
            await worker.cancel()

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
            host = await db.get(User, property_.user_id)
            session = await call_service.get_or_create_call_session(
                db,
                exotel_call_id=exotel_call_id,
                property_id=property_.id,
                guest_profile_id=guest.id if guest else None,
                caller_number=caller_number,
                user_id=property_.user_id,
            )
            system_prompt = build_system_prompt(property_, guest, host)
            first_message = first_message_for(property_, guest, host)
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
        host = await db.get(User, property_.user_id)
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
        system_prompt = build_system_prompt(property_, None, host)
        first_message = first_message_for(property_, None, host)
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
