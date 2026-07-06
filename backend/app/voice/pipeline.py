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
from datetime import datetime, timezone

import aiohttp
from fastapi import WebSocket
from openai import RateLimitError
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
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

# Defaults (confidence=0.7, min_volume=0.6) let quiet background noise or a
# second voice near the caller's mic register as "user speaking" -- which
# broadcasts an interruption that cuts off the bot's in-progress TTS and
# forces a Sarvam TTS reconnect (~1.3s dead air, confirmed via call logs)
# over what was often just a stray sound, not the caller actually talking.
# Raising confidence/min_volume makes VAD require louder, more confident
# speech before it fires -- filters out background noise without adding
# latency to genuine interruptions (start_secs/stop_secs, the actual timing
# knobs, are left at their defaults).
_VAD_PARAMS = VADParams(confidence=0.85, min_volume=0.7)


def _pick_groq_model() -> str:
    """First model in settings.groq_models that app.main's periodic
    _check_llm_health hasn't marked down (e.g. via a 429 from hitting a
    model's free-tier rate limit). Falls back to the first model in the list
    if health data isn't populated yet (cold start, before the first check
    has run) -- same as the pre-fallback-chain default behavior."""
    from app.main import llm_health

    for model in settings.groq_models:
        health = llm_health.get(model)
        if health is None or health.get("ok"):
            return model
    # Every model in the chain is marked down -- still return the top-priority
    # one rather than raising. A live 429 with retry/backoff is a better
    # outcome for a caller mid-call than the pipeline failing to build at all.
    return settings.groq_models[0]


class _FallbackGroqLLMService(GroqLLMService):
    """GroqLLMService that retries a live 429 against the next model in
    settings.groq_models immediately, in the same call, instead of failing
    the turn and waiting for the next 60s app.main._check_llm_health pass to
    notice and reroute (see _pick_groq_model). The 60s health check still
    runs and still front-loads calls onto a known-good model -- this only
    covers the gap where a model goes bad *between* checks, which is
    exactly what produced the 429 in production logs on 2026-07-04.
    """

    def create_client(self, api_key=None, base_url=None, **kwargs):
        # BaseOpenAILLMService.create_client (the implementation
        # GroqLLMService.create_client delegates to) builds AsyncOpenAI(...)
        # from only its named params -- it accepts **kwargs but never
        # forwards them, so passing max_retries here would silently be
        # dropped. Building the client directly is the only way to actually
        # set it.
        #
        # Why it matters: AsyncOpenAI retries a 429 internally by default
        # (max_retries=2, with backoff that can honor Groq's Retry-After
        # header -- sometimes several seconds) before ever raising
        # RateLimitError up to get_chat_completions below. Left at the
        # default, the SDK would blindly re-hit the SAME already-rate-limited
        # model twice before this class gets a chance to move to the next
        # model in the chain -- exactly the multi-second live-call delay this
        # fallback exists to avoid. max_retries=0 makes a 429 raise
        # immediately so the only retry that happens is the fast, cross-model
        # one below.
        import httpx
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            http_client=DefaultAsyncHttpxClient(
                limits=httpx.Limits(max_keepalive_connections=100, max_connections=1000, keepalive_expiry=None)
            ),
        )

    async def get_chat_completions(self, context):
        from app.main import llm_health

        # Start from whichever model _build_llm already picked (the first
        # one _pick_groq_model found healthy) rather than restarting from
        # settings.groq_models[0] -- otherwise a call that correctly started
        # on a fallback model (because the top-priority one was already
        # marked down by the periodic health check) would get silently
        # bounced back to that known-bad model here on every single turn.
        starting_model = self._settings.model
        ordered_models = [starting_model, *[m for m in settings.groq_models if m != starting_model]]

        last_error: RateLimitError | None = None

        for model in ordered_models:
            self._settings.model = model
            self._settings.extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
            try:
                return await super().get_chat_completions(context)
            except RateLimitError as e:
                logger.warning("Groq model %s hit a live 429 mid-call, trying next in chain: %s", model, e)
                llm_health[model] = {
                    "ok": False,
                    "latency_s": None,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(e),
                }
                last_error = e

        # Every model in the chain 429'd within this single call -- raise the
        # last error rather than looping forever or silently returning
        # nothing, same failure mode as before this fallback existed.
        raise last_error


def _build_llm():
    if settings.llm_provider == "anthropic":
        return AnthropicLLMService(
            api_key=settings.anthropic_api_key,
            settings=AnthropicLLMService.Settings(model=settings.anthropic_model),
        )
    if settings.llm_provider == "openrouter":
        return _build_openrouter_llm()
    # openai/gpt-oss-120b (and the other models in groq_models) default to
    # "medium" reasoning effort on Groq, which adds a hidden chain-of-thought
    # pass before every reply -- a real source of multi-second latency on a
    # phone call. "low" trades reasoning depth for speed, which is the right
    # tradeoff for live conversational replies.
    #
    # If every model in settings.groq_models is marked down in llm_health
    # (e.g. Groq is down account-wide, not just one model's rate limit),
    # OpenRouter -- when configured -- is the last resort rather than
    # retrying the same rate-limited Groq model the caller would otherwise
    # be stuck waiting on.
    from app.main import llm_health

    if settings.openrouter_api_key and all(
        not llm_health.get(model, {}).get("ok", True) for model in settings.groq_models
    ):
        logger.warning("All Groq models marked down in llm_health -- falling back to OpenRouter for this call")
        return _build_openrouter_llm()

    model = _pick_groq_model()
    # reasoning_effort is gpt-oss-specific -- other models in the fallback
    # chain (e.g. llama-3.1-8b-instant) reject it outright with a 400, which
    # would break the exact call this fallback exists to save.
    extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
    return _FallbackGroqLLMService(
        api_key=settings.groq_api_key,
        settings=GroqLLMService.Settings(model=model, extra=extra),
    )


def _build_openrouter_llm():
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
        settings=OpenAILLMService.Settings(model=settings.openrouter_model, max_completion_tokens=900, extra=extra),
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
            settings=SarvamTTSService.Settings(
                model=settings.sarvam_tts_model,
                voice=settings.sarvam_tts_speaker,
                pace=1.15,  # slightly faster than 1.0 default for phone call cadence
            ),
        )
        llm = _build_llm()

        tools = build_voice_tools(call_session_id, property_id, host_user_id)
        # first_message is pre-seeded as an assistant turn so the LLM knows
        # it was already said (the "don't repeat greeting" rule relies on
        # this being in context) -- it is spoken directly via TTSSpeakFrame
        # below, not generated by the LLM. Letting the LLM generate the
        # opening line itself (by pushing a context frame with no user turn
        # yet) produced garbled/hallucinated output on some calls -- a fixed,
        # host-authored greeting is both faster and reliable.
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
        # for a premature trigger). It was later bumped to 1.4s, which fixed
        # that but added ~1.4s of dead air to every single turn (confirmed via
        # enable_metrics call logs -- the gap between STT delivering a
        # transcript and inference triggering matched this value exactly).
        # 0.9s is the middle ground that was actually validated against the
        # mid-sentence-interruption problem before the jump to 1.4s.
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)]
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
                and message.get("content") is not None  # skip tool-call turns (content=null)
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

        @transport.event_handler("on_client_connected")
        async def _on_connected_greeting(transport, client):
            # worker.queue_frame() injects the frame at the true SOURCE of the
            # pipeline, so it flows downstream through every real stage
            # (stt -> user_aggregator -> llm -> tts -> transport.output()).
            # STT/aggregators/LLM pass a TTSSpeakFrame through untouched since
            # it isn't audio or an LLM-input frame; TTS synthesizes it.
            # This is pipecat's own documented pattern for "bot speaks first"
            # and is the only mechanism that reliably reached TTS in testing --
            # pushing directly into llm.push_frame() or tts.push_frame() either
            # skipped TTS synthesis entirely or hit WebSocket lifecycle issues.
            try:
                await worker.queue_frame(TTSSpeakFrame(first_message))
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
            vad_analyzer=SileroVADAnalyzer(params=_VAD_PARAMS),
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
            vad_analyzer=SileroVADAnalyzer(params=_VAD_PARAMS),
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
            vad_analyzer=SileroVADAnalyzer(params=_VAD_PARAMS),
        ),
    )

    await _run_pipeline(transport, None, call_session_id, user.id, system_prompt, first_message)
