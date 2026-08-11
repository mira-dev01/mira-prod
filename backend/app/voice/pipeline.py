"""Builds and runs one Pipecat voice pipeline per call.

Two entry points share the same pipeline-building logic (`_run_pipeline`):
- run_voice_pipeline: real Exotel calls, over a WebSocket carrying Exotel's
  raw-PCM media protocol (see app/api/v1/voice.py).
- run_browser_voice_pipeline: the in-dashboard "test in browser" feature,
  over WebRTC, for testing without a real phone call.

Both feed into Sarvam STT -> app/voice/language_sync.py (mirrors the guest's
detected speech language onto TTS) -> Groq/Anthropic LLM (function-calling
into app/voice/tools.py, which wraps the unchanged business logic in
app/services/tool_handlers.py) -> Sarvam TTS.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import aiohttp
from fastapi import WebSocket
from openai import RateLimitError
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import ErrorFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.runner.types import CallData
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
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
from app.integrations import exotel_client
from app.models.property import Property
from app.models.user import User
from app.prompts.system_prompt import (
    build_lead_system_prompt,
    build_system_prompt,
    first_message_for,
    lead_first_message_for,
)
from app.schemas.call_classification import QUALIFIED_CALL_TYPES
from app.services import (
    call_classification_service,
    call_coordinator,
    call_service,
    call_summary_service,
    faq_service,
    guest_memory_service,
    lead_service,
    recovery_service,
)
from app.voice.conversation_quality import ConversationQuality
from app.voice.conversation_state import ConversationState
from app.voice.conversation_style import ConversationStyleProcessor
from app.voice.end_call_reliability_guard import EndCallReliabilityGuardProcessor
from app.voice.escalation_phrase_guard import EscalationPhraseGuardProcessor
from app.voice.language_sync import DEFAULT_TTS_LANGUAGE, LanguageSyncProcessor
from app.voice.meta_commentary_guard import MetaCommentaryGuardProcessor
from app.voice.premature_end_call_guard import PrematureEndCallGuardProcessor
from app.voice.property_recommendation_guard import PropertyRecommendationGuardProcessor
from app.voice.redundant_context_guard import RedundantContextGuardProcessor
from app.voice.repetition_guard import RepetitionGuardProcessor
from app.voice.response_shape_guard import ResponseShapeValidatorProcessor
from app.voice.ringing_audio import play_busy_message, play_ringing_tone
from app.voice.silence_watchdog import SilenceWatchdogProcessor
from app.voice.slow_tool_filler import SlowToolFillerProcessor
from app.voice.state_prompt_sync import StatePromptSyncProcessor
from app.voice.style_compliance_monitor import StyleComplianceMonitor
from app.voice.tools import build_voice_tools
from app.voice.turn_strategies import HybridCompletenessUserTurnStopStrategy
from app.voice.vad import create_vad_analyzer

logger = logging.getLogger(__name__)

# Holds strong references to detached "fire and forget" tasks created below
# (currently the Exotel hangup task, spawned from both on_pipeline_finished
# and _run_pipeline's own construction-failure safety net -- see
# _hangup_exotel_call) for their entire lifetime. asyncio.create_task()
# itself only returns a Task; per
# asyncio's own documentation, if nothing keeps a reference to that Task
# object, it "can be garbage collected at any time, even before it's done" --
# discarding the return value (as `asyncio.create_task(coro())` does with no
# assignment) is exactly that case. This set is that reference: each task
# adds itself on creation and removes itself via a done-callback once it
# actually finishes, so the set's steady-state size is just "how many of
# these are in flight right now," not an ever-growing leak.
_background_tasks: set[asyncio.Task] = set()


def _spawn_background_task(coro) -> asyncio.Task:
    """asyncio.create_task() that survives GC -- see _background_tasks."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _hangup_exotel_call(exotel_call_id: str) -> None:
    """The one place that calls exotel_client.hangup_call from this module --
    shared by on_pipeline_finished (normal end-of-call) and _run_pipeline's
    own finally block (construction-failure safety net, see its call site's
    comment) so the try/except/log shape and the "call_terminated" log line
    exist exactly once, not duplicated per caller. Always run via
    _spawn_background_task, never awaited inline -- see either call site for
    why (Exotel's REST latency must never gate pipeline/sink teardown).
    Safe to call from both: exotel_client.hangup_call's own per-call_sid
    idempotency guard makes a second call here a same-process no-op, not a
    second real HTTP request."""
    try:
        await exotel_client.hangup_call(exotel_call_id)
        logger.info("call_terminated call_id=%s", exotel_call_id)
    except Exception:
        logger.exception("Failed to hang up Exotel call %s", exotel_call_id)


# Defaults (confidence=0.7, min_volume=0.6) let quiet background noise or a
# second voice near the caller's mic register as "user speaking" -- which
# broadcasts an interruption that cuts off the bot's in-progress TTS and
# forces a Sarvam TTS reconnect (~1.3s dead air, confirmed via call logs)
# over what was often just a stray sound, not the caller actually talking.
# Raising confidence/min_volume makes VAD require louder, more confident
# speech before it fires -- filters out background noise without adding
# latency to genuine interruptions.
#
# start_secs raised from pipecat's own default (0.2s) to 0.35s -- 0.2s only
# needs a sound to persist a fifth of a second before VAD confirms "speech
# started" and fires an interruption, closer to a mic bump or a breath than
# real speech. Guest feedback (2026-07-23): the bot was cutting the guest
# off too readily. 0.35s asks for a bit more sustained sound before
# confirming an interruption, without being so long it makes the bot feel
# unresponsive to a genuine interruption -- needs a real call to confirm
# this is the right number, not just a plausible one.
_VAD_PARAMS = VADParams(confidence=0.85, min_volume=0.7, start_secs=0.35)

# Per-host voice gender (User.agent_voice_gender, "female" | "male") maps to
# one representative Sarvam bulbul:v3 speaker name each -- the v3 speaker
# enum itself carries no gender metadata (unlike the older v2 enum, which
# did), so this mapping is MIRA's own. "female" keeps settings.sarvam_tts_speaker
# ("roopa") as the fallback so an unset/unrecognized gender behaves exactly
# as it did before this field existed.
VOICE_BY_GENDER = {
    "female": settings.sarvam_tts_speaker,
    "male": "shubh",
}


def _pick_groq_model() -> str:
    """First model in settings.groq_models that app.main's periodic
    _check_llm_health hasn't marked down (e.g. via a 429 from hitting a
    model's per-account rate limit -- paid Groq plan as of 2026-07-07, not
    free tier, but not unlimited). Falls back to the first model in the list
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
            # reasoning_format isn't a typed parameter on the OpenAI/Groq SDK's
            # AsyncCompletions.create() -- passing it as a bare kwarg (like
            # reasoning_effort, which IS typed) raises "unexpected keyword
            # argument" on every single completion call, confirmed live (the
            # guest got no response at all, repeatedly, for the whole call).
            # extra_body is the SDK's own sanctioned escape hatch for
            # provider-specific fields outside its typed surface -- it merges
            # into the raw outgoing JSON body instead of being validated as a
            # named parameter.
            self._settings.extra = (
                {"reasoning_effort": "low", "extra_body": {"reasoning_format": "hidden"}}
                if "gpt-oss" in model
                else {}
            )
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


class _ReconnectingSarvamSTTService(SarvamSTTService):
    """SarvamSTTService.run_stt has no reconnect-on-failure logic at all --
    confirmed live on 2026-07-20: Sarvam closed the STT websocket
    server-side mid-call (close code 1000, reason "ASR model call failed",
    a transient failure on Sarvam's own backend), and every subsequent
    audio chunk immediately re-raised the same error against the now-dead
    socket, forever -- 500+ identical ErrorFrames logged within 4 seconds,
    with the guest's audio silently dropped and Mira never responding again
    for the rest of the call. _connect/_disconnect are already pipecat's own
    sanctioned reconnect pair (SarvamSTTService calls them itself when
    settings or the prompt change mid-call -- see _update_settings/
    _set_prompt), so reusing them here for a dead-connection error follows
    the same pattern rather than inventing a new one.
    """

    _RECONNECT_COOLDOWN_SECONDS = 3.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stt_reconnecting = False
        self._stt_last_reconnect_attempt = 0.0

    async def run_stt(self, audio: bytes):
        import time

        async for frame in super().run_stt(audio):
            if isinstance(frame, ErrorFrame) and "Error sending audio to Sarvam" in (frame.error or ""):
                now = time.monotonic()
                if (
                    not self._stt_reconnecting
                    and (now - self._stt_last_reconnect_attempt) > self._RECONNECT_COOLDOWN_SECONDS
                ):
                    self._stt_reconnecting = True
                    self._stt_last_reconnect_attempt = now
                    logger.warning("Sarvam STT connection appears dead -- reconnecting: %s", frame.error)
                    try:
                        await self._disconnect()
                        await self._connect()
                        logger.info("Sarvam STT reconnected successfully")
                    except Exception:
                        logger.exception("Failed to reconnect Sarvam STT")
                    finally:
                        self._stt_reconnecting = False
                # Swallow this specific error frame instead of forwarding it --
                # on a genuinely dead connection it would otherwise repeat for
                # every single audio chunk (confirmed live: every ~100ms) and
                # drown out every other log line for the rest of the call.
                continue
            yield frame


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
    # reasoning_effort/reasoning_format are gpt-oss-specific -- other models
    # in the fallback chain (e.g. llama-3.1-8b-instant) reject them outright
    # with a 400, which would break the exact call this fallback exists to
    # save. reasoning_format="hidden" is required, not cosmetic: without it,
    # Groq's gpt-oss models default to embedding their raw chain-of-thought
    # directly in the same content field as the actual reply (confirmed
    # live -- guest heard "User wants to confirm booking. We need to
    # finalize..." spoken as part of the response), since pipecat's
    # GroqLLMService has no separate handling for a reasoning channel and
    # just speaks whatever comes back in content. It must be passed via
    # extra_body, not as a bare kwarg -- unlike reasoning_effort, it isn't a
    # parameter the OpenAI/Groq SDK's create() actually recognizes, and a
    # bare kwarg it doesn't recognize raises "unexpected keyword argument" on
    # every single completion call (confirmed live -- this broke ALL
    # responses for an entire call, not just leaked reasoning text).
    # extra_body is the SDK's own sanctioned escape hatch for exactly this.
    extra = (
        {"reasoning_effort": "low", "extra_body": {"reasoning_format": "hidden"}} if "gpt-oss" in model else {}
    )
    return _FallbackGroqLLMService(
        api_key=settings.groq_api_key,
        # max_completion_tokens caps a single reply -- the Groq path had no
        # cap at all until this fix. Confirmed live 2026-07-27: a single turn
        # on a long, noisy (mixed-script, low-signal) call came back as 3072
        # completion tokens -- the same clarifying question paraphrased
        # dozens of times back to back, all spoken to the guest -- versus a
        # normal reply's tens of tokens. reasoning_format="hidden" above was
        # working correctly on that same completion (reasoning tokens were
        # separately reported, not the cause) -- this was the model's actual
        # answer content degenerating into repetition, a distinct failure
        # mode. 400 gives real headroom over the longest legitimate single
        # turn (e.g. describing three recommended properties in one sentence
        # each) while making a multi-thousand-token repetition loop
        # impossible -- it gets hard-truncated instead of running away.
        # GroqLLMSettings is a dataclass field, set once here and never
        # touched by _FallbackGroqLLMService's per-model retry loop (which
        # only reassigns .model/.extra), so it stays capped across every
        # model in the fallback chain, not just the first one tried.
        settings=GroqLLMService.Settings(model=model, extra=extra, max_completion_tokens=400),
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
    #
    # reasoning: {"exclude": true} is OpenRouter's own unified param for
    # dropping reasoning tokens from the response entirely -- the equivalent
    # of Groq's reasoning_format="hidden" (see _build_llm's comment on that).
    # Without it, gpt-oss-120b via OpenRouter returns raw harmony-format
    # chain-of-thought inline in the same `content` field pipecat's
    # OpenAILLMService just speaks verbatim -- confirmed live 2026-07-27:
    # "We need to recommend properties for 10 guests, 3 nights... Use
    # recommend_properties with num_guests maybe 5?" was spoken directly to
    # a guest. This path is reachable in production: OPENROUTER_MODEL is
    # configured to openai/gpt-oss-120b as the last-resort fallback when
    # every Groq model in the chain is rate-limited. Must go in extra_body,
    # not as a bare kwarg -- same reasoning as reasoning_format above, it
    # isn't a parameter the OpenAI-compatible SDK's create() recognizes.
    extra = (
        {"reasoning_effort": "low", "extra_body": {"reasoning": {"exclude": True}}}
        if "gpt-oss" in settings.openrouter_model
        else {}
    )
    return OpenAILLMService(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        settings=OpenAILLMService.Settings(model=settings.openrouter_model, max_completion_tokens=900, extra=extra),
    )


# Matches call_coordinator.renew()'s own long-standing docstring ("Renewed
# roughly every 20-30s"); DEFAULT_LEASE_TTL is 45s, so this cadence leaves a
# wide safety margin against a single missed tick.
_LEASE_RENEWAL_INTERVAL_SECONDS = 20


async def _renew_call_lease_periodically(
    host_user_id: uuid.UUID, property_id: uuid.UUID | None, token: str
) -> None:
    """Keeps a CallCoordinator lease alive for the duration of a live call.
    Runs as a background task started alongside the pipeline and cancelled
    in _run_pipeline's finally block, same lifecycle as ringing_audio_task.

    Redis migration: no DB session at all anymore -- call_coordinator.renew()
    is a single Redis round trip (one atomic Lua EVAL), not a Postgres
    UPDATE, so there is no per-tick session to open/close the way the old
    Postgres-backed version needed. token is the fencing credential
    call_coordinator.acquire() issued; renewal only succeeds if it still
    matches the currently-stored lease (see call_coordinator.py's module
    docstring on why this must be token-checked, not just holder_ref-keyed).

    Fails open: a renewal failure (Redis outage, or the lease already
    reclaimed by a different caller because it genuinely expired) is logged
    and this loop simply stops -- it must never raise into the live call.
    Losing lease protection mid-call is a real but bounded downside (a
    subsequent busy-rejection could wrongly fall through to START_PIPELINE);
    crashing the guest's live call to protect a lease would be strictly
    worse.
    """
    try:
        while True:
            await asyncio.sleep(_LEASE_RENEWAL_INTERVAL_SECONDS)
            new_expiry = await call_coordinator.renew(host_user_id, property_id, token)
            if new_expiry is None:
                logger.warning(
                    "CallCoordinator lease renewal found no active lease for host_user_id=%s property_id=%s",
                    host_user_id,
                    property_id,
                )
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "CallCoordinator lease renewal failed for host_user_id=%s property_id=%s", host_user_id, property_id
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
    guest_profile_id: uuid.UUID | None = None,
    guest_known_name: str | None = None,
    exotel_call_id: str | None = None,
    ringing_audio_task: asyncio.Task | None = None,
    voice_gender: str = "female",
    call_lease_token: str | None = None,
) -> None:
    # NOTE: we deliberately do NOT create a Lead row up front. Doing so gave
    # every connection attempt its own empty lead, and a browser/ICE
    # reconnect (a second, short-lived call session that never reaches
    # on_pipeline_finished) would leave its empty row uncleaned -- surfacing
    # as a phantom "unknown guest" duplicate next to the real, enriched lead.
    # A lead now comes into existence only from real qualification during the
    # call (update_lead / escalate_to_host, both keyed by call_session_id, so
    # exactly one row per session), and the caller's phone / the property
    # discussed are backfilled onto it at call end (see on_pipeline_finished).
    # A call where the agent captured nothing simply leaves no lead behind.
    #
    # Cleanup-pass fix: call_coordinator.DEFAULT_LEASE_TTL is 45s, and
    # nothing previously renewed the lease during a live call -- any real
    # call longer than 45s silently lost its CallCoordinator lease mid-call,
    # so a second incoming call to the same host/property after that point
    # was wrongly given START_PIPELINE instead of BUSY_RECOVERY. This starts
    # the same periodic renewal call_coordinator.renew()'s own docstring
    # always assumed existed ("Renewed roughly every 20-30s by a caller
    # during a live call"); cancelled in the finally block below alongside
    # the existing release() call.
    renewal_task = (
        asyncio.create_task(_renew_call_lease_periodically(host_user_id, property_id, call_lease_token))
        if call_lease_token is not None
        else None
    )
    try:
        await _run_pipeline_inner(
            transport,
            property_id,
            call_session_id,
            host_user_id,
            system_prompt,
            first_message,
            caller_number=caller_number,
            property_name=property_name,
            guest_profile_id=guest_profile_id,
            guest_known_name=guest_known_name,
            exotel_call_id=exotel_call_id,
            ringing_audio_task=ringing_audio_task,
            voice_gender=voice_gender,
        )
    finally:
        if renewal_task is not None and not renewal_task.done():
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass

        # Guarantees the ringing-tone task (see app/voice/ringing_audio.py)
        # is always stopped, even if _run_pipeline_inner raises before
        # reaching its own cancel point (e.g. SarvamTTSService/_build_llm
        # construction failing) -- without this, a build-time failure would
        # leave the task looping forever, writing to a websocket whose fate
        # is no longer being managed by anything.
        if ringing_audio_task is not None and not ringing_audio_task.done():
            ringing_audio_task.cancel()
            try:
                await ringing_audio_task
            except asyncio.CancelledError:
                pass

        # Guarantees Exotel is told to hang up even if _run_pipeline_inner
        # raises during construction (STT/LLM/TTS/Pipeline/PipelineWorker
        # all fallible, all built before on_pipeline_finished is registered
        # -- see that handler's own docstring inside _run_pipeline_inner)
        # -- confirmed as a real gap: on a construction failure,
        # on_pipeline_finished never registers, so its hangup_call never
        # fires, and the guest is left connected on a line no code is
        # driving toward hangup (the websocket itself stays open; only
        # runner.run() returning, or the transport, would close it, and
        # neither happens here). Same fire-and-forget shape as
        # on_pipeline_finished's own hangup (_spawn_background_task, not
        # awaited -- Exotel's REST latency must not gate this finally
        # block returning), and the SAME exotel_client.hangup_call, whose
        # own per-call_sid idempotency guard (see app/integrations/
        # exotel_client.py) is what makes it safe to call unconditionally
        # here rather than only on the exception path: on the success path
        # on_pipeline_finished has typically already requested (or is
        # already requesting) the real hangup, so this becomes a same-
        # process no-op (an in-memory set lookup, no second HTTP call) --
        # no new "did construction fail" flag needed, no risk of masking
        # which path actually ran, and no behavior change on success.
        if exotel_call_id:
            # Deliberately unconditional (not "only if _run_pipeline_inner
            # raised") -- see the comment above for why that's safe and
            # simpler than tracking a separate failure flag. Logged as its
            # own event name (not call_hangup_requested, which
            # on_pipeline_finished already logs on the normal path) so a
            # real construction failure is distinguishable in logs from
            # this finally block's routine, idempotent no-op on every
            # ordinary successful call.
            logger.info("call_hangup_finally_block_requested call_id=%s", exotel_call_id)
            _spawn_background_task(_hangup_exotel_call(exotel_call_id))

        # Guarantees the CallCoordinator lease acquired in run_voice_pipeline
        # (see app/services/call_coordinator.py) is always released here too,
        # not just on the happy path through on_pipeline_finished -- same
        # reasoning as the ringing-tone cleanup above: _run_pipeline_inner
        # can raise before ever reaching on_pipeline_finished's registration
        # (e.g. SarvamTTSService/_build_llm construction failing), and
        # without this, that lease would sit held until its TTL lazily
        # expires (Redis TTL now, not a Postgres lazy-reclaim check), wrongly
        # blocking a real subsequent call to the same host/property. None on
        # the BUSY_RECOVERY / fail-open-without-lease paths (see
        # run_voice_pipeline) -- release() is idempotent and a no-op for a
        # token with no active lease either way, but the None check avoids
        # an unnecessary Redis round trip. Redis migration: no DB session
        # here anymore -- release() is a single atomic Lua call, not a
        # Postgres UPDATE.
        if call_lease_token is not None:
            try:
                lease_actually_released = await call_coordinator.release(host_user_id, property_id, call_lease_token)
            except Exception:
                lease_actually_released = False
                logger.exception(
                    "Failed to release CallCoordinator lease for host_user_id=%s property_id=%s",
                    host_user_id,
                    property_id,
                )

            # Busy Call Recovery, availability half: fires ONLY when THIS
            # call's release() call actually freed the (host, property)
            # lease (release() returns True only for that case -- False
            # for a Redis outage, a stale token, or a token that never held
            # anything; see call_coordinator.release's own docstring for
            # why that distinction is safe to expose). CallCoordinator/
            # lease state is deliberately the trigger, not "pipeline
            # finished" or "on_pipeline_finished ran" -- a call that never
            # actually held the lease (e.g. BUSY_RECOVERY's own fail-open
            # path) or a stale/duplicate release must never fire this, or
            # a busy-recovery guest could be told "Mira is available now"
            # while Mira is, from CallCoordinator's own source of truth,
            # still busy. Fired detached (_spawn_background_task, same
            # GC-safety mechanism as the Exotel hangup task above) --
            # process_availability_recovery does its own WhatsApp sends,
            # which must never delay this already-ending call's teardown.
            # CallCoordinator itself has no idea this happens: this is
            # pipeline.py, the lease-owning caller, reacting to release()'s
            # own return value -- see recovery_service.py's own docstring
            # for the CallCoordinator-stays-unaware half of this contract.
            if lease_actually_released:
                logger.info(
                    "availability_processing_triggered host_user_id=%s property_id=%s",
                    host_user_id,
                    property_id,
                )
                _spawn_background_task(recovery_service.process_availability_recovery(host_user_id, property_id))


async def _run_pipeline_inner(
    transport: BaseTransport,
    property_id: uuid.UUID | None,
    call_session_id: uuid.UUID,
    host_user_id: uuid.UUID,
    system_prompt: str,
    first_message: str,
    caller_number: str | None = None,
    property_name: str | None = None,
    guest_profile_id: uuid.UUID | None = None,
    guest_known_name: str | None = None,
    exotel_call_id: str | None = None,
    ringing_audio_task: asyncio.Task | None = None,
    voice_gender: str = "female",
) -> None:
    stt = _ReconnectingSarvamSTTService(
        api_key=settings.sarvam_api_key,
        model=settings.sarvam_stt_model,
        mode="codemix",  # transcribe Hindi/English/Hinglish as spoken, no translation
    )
    # Moved up from just before `tools`/`context` construction (Phase 1,
    # documentation/agent-conversation-improvement.md) -- state_prompt_sync
    # and language_sync below both need a reference to this same instance at
    # construction time, same as redundant_context_guard needs
    # silence_watchdog. Nothing between here and the original construction
    # point depended on it not existing yet.
    conversation_state = ConversationState(
        selected_property_id=str(property_id) if property_id else None,
        selected_property_name=property_name,
    )
    # Response output architecture redesign: the analytics-only home for
    # validator output (StyleComplianceMonitor, ResponseShapeValidatorProcessor
    # on a shape violation) -- constructed fresh per call, same lifetime as
    # conversation_state, never read by anything except state_prompt_sync's
    # one narrow pending_style_correction bridge. See
    # app/voice/conversation_quality.py for the full architecture boundary.
    conversation_quality = ConversationQuality()
    # Phase 3.1: passes conversation_state so the guest's detected spoken
    # language (the same signal that already drives the TTS switch) is also
    # fed back into state for the prompt layer to read -- see
    # app/voice/language_sync.py's own docstring. Unmodified by the
    # Conversation Style Engine below -- this still owns the live TTS switch
    # and ConversationState.current_spoken_language/explicit_language_preference
    # exactly as before.
    language_sync = LanguageSyncProcessor(conversation_state)
    # Conversation Style Engine: computes ConversationState.conversation_style
    # from a rolling, hysteresis-smoothed window of the guest's own turns --
    # a higher-level, slower-moving judgment on top of language_sync's raw
    # per-turn signal, so one ambiguous/noisy turn can't flip the LLM's
    # language instruction mid-call. Reads the same TranscriptionFrame
    # independently of language_sync; writes only conversation_style, never
    # current_spoken_language/explicit_language_preference. See
    # app/voice/conversation_style.py.
    conversation_style_engine = ConversationStyleProcessor(conversation_state)
    # Auto-cuts a call where the guest has gone silent/unresponsive: nudges
    # ("Hello? Are you still there?") after each ~9s of silence, hangs up
    # after the second nudge goes unanswered. See app/voice/silence_watchdog.py
    # for why this has to live as its own processor rather than piggybacking
    # on the turn-stop strategy.
    #
    # 9.0s, not the module default of 5.0s -- confirmed live on 2026-07-23: a
    # guest recalling specific dates and phrasing an availability question in
    # Hindi hadn't finished formulating their reply by 5s (their real answer
    # landed 4s after the nudge already fired, mid-thought). 5s is plausible
    # for a yes/no but too tight for anything requiring the guest to actually
    # think and construct a sentence.
    # Phase 5 (documentation/agent-conversation-improvement.md): passes
    # conversation_state so the closing lifecycle (armed/reopened/closed) is
    # tracked as real state the prompt layer can read, not just this
    # processor's own internal hangup bookkeeping.
    silence_watchdog = SilenceWatchdogProcessor(timeout_seconds=9.0, conversation_state=conversation_state)
    # Code-level backstop for the "let me loop in the host" ban in
    # GOLDEN_RULES -- prompting alone has failed on this exact wording
    # repeatedly across real calls. See app/voice/escalation_phrase_guard.py.
    escalation_guard = EscalationPhraseGuardProcessor()
    # Drops a spurious LLM re-invocation when pipecat's own deferred
    # context-push (bundling function-call results that arrive while the
    # bot is speaking) fires with nothing new since the last completion --
    # confirmed live: caused a call to end itself with no guest reply in
    # between. Also drops any re-invocation once silence_watchdog has a
    # hangup armed/fired, closing a race that otherwise let an extra LLM
    # turn fire after end_call/decline_irrelevant_call and kept calls from
    # actually disconnecting. See app/voice/redundant_context_guard.py.
    redundant_context_guard = RedundantContextGuardProcessor(silence_watchdog)
    # Keeps ConversationState's slots/conversation_goal (Phase 1,
    # documentation/agent-conversation-improvement.md) visible to the model
    # every turn -- the system prompt is fixed at call start and can't
    # reflect anything learned mid-call on its own. Sits right after
    # redundant_context_guard (before llm) so it only ever touches a context
    # that's actually about to be forwarded. Also passes conversation_quality
    # so a confirmed style-compliance FAIL from the previous turn (recorded
    # by StyleComplianceMonitor, below) can render one turn's worth of
    # emphasized style instruction -- the one permitted, one-directional
    # bridge from ConversationQuality back to conversational behavior. See
    # app/voice/state_prompt_sync.py.
    state_prompt_sync = StatePromptSyncProcessor(conversation_state, conversation_quality)
    # Separate failure mode, same symptom: the model itself (in a single
    # turn, not a spurious re-invocation) asks a real question and calls
    # end_call together -- confirmed live. Cancels the pending hangup so the
    # guest gets a real chance to reply. See app/voice/premature_end_call_guard.py.
    premature_end_call_guard = PrematureEndCallGuardProcessor(silence_watchdog)
    # Strips leaked property_id UUIDs from speech and backstops a
    # recommend_properties call whose result never actually got named to the
    # guest -- both confirmed live. See app/voice/property_recommendation_guard.py.
    property_recommendation_guard = PropertyRecommendationGuardProcessor()
    # Speaks a short filler line ("Let me check that for you.") if
    # recommend_properties is still running after ~1.2s -- confirmed live
    # 2026-08-01: a ~30s silent gap between the guest's question and the
    # spoken recommendation, with nothing telling the guest Mira was still
    # working on it. Scoped to recommend_properties only; get_pricing/
    # check_calendar resolve fast enough (~1.5s) that the delay timer never
    # fires for them. See app/voice/slow_tool_filler.py.
    slow_tool_filler = SlowToolFillerProcessor()
    # Cuts a response short, mid-stream, the moment it starts repeating
    # itself -- the deterministic guarantee max_completion_tokens alone
    # doesn't provide (a cap bounds how much garbage CAN be generated, not
    # whether any of it repeats). Phase 4.2 (documentation/
    # agent-conversation-improvement.md) also passes conversation_state so it
    # can catch a same-fact-different-wording repeat ACROSS turns (e.g. a
    # quoted price restated later), not just within one response. See
    # app/voice/repetition_guard.py.
    repetition_guard = RepetitionGuardProcessor(conversation_state)
    # Drops narrator/stage-direction asides like "(Waiting for guest
    # response)" before they reach TTS -- confirmed live. See
    # app/voice/meta_commentary_guard.py.
    meta_commentary_guard = MetaCommentaryGuardProcessor()
    # Response output architecture redesign: a streaming OBSERVER, not a
    # blocker -- every LLMTextFrame is forwarded immediately and
    # unconditionally, regardless of what this monitor observes. Scores the
    # model's OWN original wording (placed here, before the tool-fidelity
    # guards below, deliberately -- it must never score a guard-substituted
    # safe line). Replaces the old ResponseComplianceProcessor entirely:
    # that processor buffered the whole response and, on a FAIL, made a
    # second direct LLM call to regenerate it (up to 8s of dead air) --
    # both removed. A confirmed FAIL here only ever sets
    # conversation_quality.pending_style_correction, read by
    # state_prompt_sync on the NEXT turn to ask ConversationStyle for a
    # more emphatic rendering of the SAME style -- never a rewrite of THIS
    # turn's audio. See app/voice/style_compliance_monitor.py.
    style_compliance_monitor = StyleComplianceMonitor(conversation_state, conversation_quality)
    # Final structural gate, right before tts -- catches a malformed/
    # concatenated response SHAPE (multiple questions, multiple greetings,
    # a duplicated safe-line, a punctuation flood, an unfinished trailing
    # clause, more than one recommendation block) that every guard above it
    # cannot see, since each of those targets a different, narrower failure
    # mode (a single repeated sentence, a leaked property_id, a narrator
    # aside). Phase 4.3 (documentation/agent-conversation-improvement.md).
    # Sits LAST, after every rewrite above has already run, so it validates
    # the actual final text about to be spoken.
    #
    # Response output architecture redesign: rewritten internally from
    # unconditional whole-response buffering to per-sentence streaming --
    # every LLMTextFrame is forwarded the instant its sentence completes;
    # only on an actual structural violation does it stop forwarding the
    # remainder of that one response (same accepted tradeoff
    # repetition_guard.py already uses). See app/voice/response_shape_guard.py
    # for the full mechanism and why it stays one processor, not split.
    response_shape_guard = ResponseShapeValidatorProcessor(conversation_quality)

    async with aiohttp.ClientSession() as http_session:
        tts = SarvamTTSService(
            api_key=settings.sarvam_api_key,
            aiohttp_session=http_session,
            settings=SarvamTTSService.Settings(
                model=settings.sarvam_tts_model,
                voice=VOICE_BY_GENDER.get(voice_gender, settings.sarvam_tts_speaker),
                pace=1.15,  # slightly faster than 1.0 default for phone call cadence
                # Starting language; language_sync (below) switches this live
                # once the guest is heard speaking Hindi/Hinglish.
                language=DEFAULT_TTS_LANGUAGE,
            ),
        )
        llm = _build_llm()

        # Browser test calls use a fixed placeholder identity (no real phone
        # number exists) -- never let that string flow through to tools as
        # if it were a real number to message/save.
        real_caller_number = (
            caller_number if caller_number and caller_number != call_service.BROWSER_TEST_CALLER_NUMBER else None
        )
        tools = build_voice_tools(
            call_session_id,
            property_id,
            host_user_id,
            conversation_state,
            guest_profile_id,
            caller_number=real_caller_number,
            silence_watchdog=silence_watchdog,
            property_recommendation_guard=property_recommendation_guard,
        )
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
        # Guarantees end_call actually fires whenever the model speaks
        # DEFAULT_CLOSING_PHRASE -- confirmed live: a guest-initiated
        # ("thank you") close sometimes produced the closing line as plain
        # text with no paired end_call tool call, leaving the call open
        # indefinitely. Sits LAST, after response_shape_guard (which can
        # still trim a violating response), so it checks the actual final
        # text about to be spoken. See app/voice/end_call_reliability_guard.py.
        end_call_reliability_guard = EndCallReliabilityGuardProcessor(silence_watchdog)
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
        #
        # settings.turn_detection_strategy (default "vad_fixed") gates an
        # experimental alternative -- see app/voice/turn_strategies.py and
        # config.py's comment. Local-only, shagun branch only; the default
        # path below is byte-identical to production's fixed 0.9s strategy.
        if settings.turn_detection_strategy == "hybrid_experimental":
            stop_strategy = HybridCompletenessUserTurnStopStrategy(base_timeout=0.9)
        else:
            stop_strategy = SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(stop=[stop_strategy])
            ),
        )

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                silence_watchdog,
                language_sync,
                conversation_style_engine,
                user_aggregator,
                redundant_context_guard,
                state_prompt_sync,
                llm,
                slow_tool_filler,
                repetition_guard,
                meta_commentary_guard,
                style_compliance_monitor,
                property_recommendation_guard,
                escalation_guard,
                premature_end_call_guard,
                response_shape_guard,
                end_call_reliability_guard,
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
            # Closing our end of the Voicebot Applet's WebSocket stream only
            # stops the audio stream -- it does NOT hang up the underlying
            # PSTN call on Exotel's side. Confirmed live: after end_call
            # correctly ended the pipeline, guests were left connected on a
            # silent line because nothing was telling Exotel's platform the
            # call itself should end. Never for browser test calls
            # (exotel_call_id is None there -- no real telephony call to
            # hang up).
            #
            # Fired as a detached task, NOT awaited, deliberately: this
            # handler runs synchronously inside the pipeline worker's own
            # EndFrame drain (worker.py's _sink_push_frame awaits
            # on_pipeline_finished directly), so awaiting hangup_call's real
            # httpx round trip to Exotel's REST API here gated the entire
            # pipeline/sink teardown on Exotel's API latency -- confirmed as
            # the source of a 2-3s caller-perceived delay after the final
            # TTS audio had already finished playing (BotStoppedSpeakingFrame
            # had already fired; the wait was purely for this HTTP call).
            # There is nothing below in this handler that depends on the
            # hangup having completed, so detaching it changes only when the
            # REST call fires relative to teardown bookkeeping, never
            # whether it fires, and never cuts off audio (which is already
            # gated upstream in silence_watchdog.py on BotStoppedSpeakingFrame,
            # not touched here). A failure is logged from inside the task,
            # same as before -- it never propagates back into this handler.
            #
            # Lifecycle safety, traced against pipecat 1.6.0's actual
            # WorkerRunner/TaskManager (see venv/.../pipecat/workers/runner.py
            # and .../pipecat/utils/asyncio/task_manager.py): neither
            # WorkerRunner._cancel_spawned_tasks() (only cancels each worker's
            # own registered runner_task) nor TaskManager.cleanup() (only
            # tracks tasks created through ITS OWN create_task(), which this
            # bare asyncio.create_task() call never goes through) has any way
            # to see or cancel this task -- pipeline/sink teardown proceeding
            # in parallel cannot reach in and cancel it. The one real risk is
            # a plain Python/asyncio footgun, not a pipecat one: a Task with
            # no surviving reference is eligible for GC before it completes
            # (see asyncio.create_task's own docs). _spawn_background_task
            # (module-level, above) closes that by holding a strong reference
            # in _background_tasks for the task's entire lifetime.
            if exotel_call_id:
                logger.info("call_hangup_requested call_id=%s", exotel_call_id)
                _spawn_background_task(_hangup_exotel_call(exotel_call_id))

            transcript = "\n".join(
                f"{message.get('role')}: {message.get('content')}"
                for message in context.messages
                if message.get("role") in ("user", "assistant")
                and message.get("content") is not None  # skip tool-call turns (content=null)
            )
            async with AsyncSessionLocal() as finalize_db:
                finalized_session = await call_service.finalize_call_session(finalize_db, call_session_id, transcript)

                duration_seconds = None
                if finalized_session is not None and finalized_session.started_at and finalized_session.ended_at:
                    duration_seconds = (finalized_session.ended_at - finalized_session.started_at).total_seconds()

                # Awaited inline (not fire-and-forget like guest memory below)
                # because lead suppression a few lines down is gated on this
                # result -- the guest has already disconnected by the time
                # on_pipeline_finished fires, so this latency is invisible to
                # them, it only delays how quickly the row settles.
                classification = await call_classification_service.classify_call(transcript, duration_seconds)
                await call_service.set_call_classification(finalize_db, call_session_id, classification)

                # Same one-shot-LLM-after-call-ends shape as classification
                # above, and just as safe to await inline -- summarize_call
                # never raises (see call_summary_service.py), so this can't
                # itself crash on_pipeline_finished.
                summary = await call_summary_service.summarize_call(transcript, duration_seconds)
                await call_service.set_call_summary(finalize_db, call_session_id, summary)

                if any(m.get("role") == "user" for m in context.messages):
                    # Backfill the real caller's phone (from Exotel) and the
                    # property this call was about onto the lead the agent
                    # created during the call. backfill_lead only fills blank
                    # fields and NEVER creates a lead -- so a call where the
                    # agent captured nothing leaves no row (no "unknown guest"
                    # phantom), while a genuine phone lead still gets the
                    # caller's number even if the guest never said it aloud.
                    backfill: dict = {}
                    if caller_number and caller_number != call_service.BROWSER_TEST_CALLER_NUMBER:
                        backfill["phone"] = caller_number
                    if property_name:
                        backfill["properties_discussed"] = [property_name]
                    # Guest Memory's on-file name (kept fresh across calls by
                    # guest_memory_service.update_guest_memory_from_call) for
                    # a returning guest who didn't restate their name this
                    # call -- backfill_lead only fills a still-blank field,
                    # so this never overwrites a name the guest actually gave
                    # during THIS call. Without this, a call with no spoken
                    # name left Lead.guest_name blank, and the dashboard's
                    # Calls page fell back to CallSession's own computed
                    # guest_name property instead -- same profile name, but
                    # only the Calls page read it that way, not Leads.
                    if guest_known_name:
                        backfill["guest_name"] = guest_known_name
                    if backfill:
                        await lead_service.backfill_lead(finalize_db, call_session_id, **backfill)
                else:
                    # A connection blip that never became a conversation --
                    # drop any near-empty lead a stray tool call may have made.
                    await lead_service.delete_if_empty(finalize_db, call_session_id)

                # Classification overrides whatever the live tool calls did --
                # a JUNK/INCOMPLETE/UNKNOWN call must never surface as a Lead,
                # even if update_lead/escalate_to_host captured real-looking
                # data mid-call (the full-transcript review is more informed
                # than in-call judgment). Runs regardless of which branch
                # above fired, since escalate_to_host can create a Lead
                # directly, independent of the backfill/delete_if_empty path.
                if classification.call_type not in QUALIFIED_CALL_TYPES:
                    await lead_service.delete_for_unqualified_call(finalize_db, call_session_id)

            # Guest Memory (memory-architecture-plan.md section 1) -- fire
            # detached in its own session, after the lead backfill/cleanup
            # above has resolved, so it reads the lead's final state and
            # never adds latency to call teardown. A guest with no
            # resolvable profile (no caller_number) or no lead this call
            # (nothing captured) is a no-op, not an error.
            async def _update_guest_memory():
                try:
                    async with AsyncSessionLocal() as guest_memory_db:
                        await guest_memory_service.update_guest_memory_from_call(
                            guest_memory_db, call_session_id, guest_profile_id, property_id, property_name
                        )
                except Exception:
                    logger.exception("Guest memory update failed for call_session_id=%s", call_session_id)

            asyncio.create_task(_update_guest_memory())

        greeting_sent = False

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
            #
            # SmallWebRTC can fire on_client_connected more than once per call
            # (ICE renegotiation) -- without this guard the greeting gets
            # queued again on the second firing and the guest hears it twice.
            nonlocal greeting_sent
            if greeting_sent:
                return
            greeting_sent = True
            try:
                # append_to_context=False: first_message is already seeded into
                # context.messages above (as an assistant turn, so the LLM knows
                # it was said). TTSSpeakFrame defaults append_to_context=True,
                # and pipecat's assistant aggregator commits any TTS-driven
                # utterance to context on its own (_handle_tts_started /
                # _handle_push_aggregation in llm_response_universal.py) --
                # left at the default, the greeting was being written into
                # context.messages a second time on every call, appearing
                # twice in a row in the transcript even with no reconnect.
                await worker.queue_frame(TTSSpeakFrame(first_message, append_to_context=False))
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

        # The ringing-tone task (see app/voice/ringing_audio.py) writes
        # directly to the same raw websocket this transport wraps -- it must
        # be fully stopped before runner.run() below, which is what starts
        # the transport's own writes (StartFrame -> transport.start() ->
        # first real audio out), or the two would race on the same socket.
        # Cancelling this late (right before handing the socket to the real
        # pipeline, not any earlier) is what lets the ringing tone cover as
        # much of the setup above -- STT/TTS build, LLM build, tool wiring --
        # as it can, while guaranteeing it can never still be playing once
        # Mira's own audio starts: cancel() + await here only returns once
        # the ringing loop has actually unwound (see ringing_audio.py's
        # docstring for why that ordering is airtight, not just usually fine).
        if ringing_audio_task is not None:
            ringing_audio_task.cancel()
            try:
                await ringing_audio_task
            except asyncio.CancelledError:
                pass

        runner = WorkerRunner()
        await runner.add_workers(worker)
        await runner.run()


async def _reject_call_as_busy(
    websocket: WebSocket,
    call_data: CallData,
    ringing_audio_task: asyncio.Task | None,
    exotel_call_id: str | None,
) -> None:
    """The guest-facing side of a BUSY_RECOVERY decision: stop the ring
    tone, play the placeholder clip, hang up, close the socket. Pulled out
    of run_voice_pipeline (principal-review cleanup) so the busy-check in
    that function reads as one clear early gate ("if busy: reject and
    return") instead of a large inline block straddling the middle of what
    is otherwise pure "resolve and build a normal call" logic. Nothing here
    is new behavior -- same steps, same order, same fail-open exception
    handling as before this was extracted.
    """
    logger.info("busy_recovery_started call_id=%s", exotel_call_id)
    if ringing_audio_task is not None:
        ringing_audio_task.cancel()
        try:
            await ringing_audio_task
        except asyncio.CancelledError:
            pass
    if call_data.stream_id:
        await play_busy_message(websocket, call_data.stream_id)
    if exotel_call_id:
        logger.info("busy_recovery_hangup call_id=%s", exotel_call_id)
        try:
            await exotel_client.hangup_call(exotel_call_id)
        except Exception:
            logger.exception("Failed to hang up busy-recovery call %s", exotel_call_id)
    try:
        await websocket.close()
    except RuntimeError:
        pass  # caller already disconnected


async def run_voice_pipeline(websocket: WebSocket, call_data: CallData) -> None:
    exotel_call_id = call_data.call_id
    dialed_number = call_data.to_number
    caller_number = call_data.from_number
    # Set only on the BUSY_RECOVERY exit -- fires recovery_service AFTER the
    # `async with AsyncSessionLocal() as db:` block below has fully closed,
    # not from inside it. Keeps the two DB lifetimes (this call's own `db`,
    # and recovery_service's separate AsyncSessionLocal()) strictly
    # sequential rather than overlapping -- good hygiene on its own, and
    # avoids any doubt about the two interacting, on top of the specific,
    # already-fixed hazard documented at busy_recovery_property_id below
    # (reading an ORM attribute on an object from THIS session after this
    # session's own commit).
    busy_recovery_args: dict | None = None

    # Plays a looping ring tone directly on the raw websocket (see
    # app/voice/ringing_audio.py) while everything below -- DB lookups, then
    # the real pipeline build and Sarvam STT/TTS connect inside
    # _run_pipeline -- is still in progress. Confirmed via call logs this
    # setup takes ~4-6s total, which the guest otherwise hears as dead air
    # before Mira's first word. Cancelled just before _run_pipeline hands the
    # socket to the real transport (see the comment at that call site) --
    # every exit path below (early return or the normal path into
    # _run_pipeline) must account for stopping it.
    ringing_audio_task = (
        asyncio.create_task(play_ringing_tone(websocket, call_data.stream_id)) if call_data.stream_id else None
    )

    async with AsyncSessionLocal() as db:
        property_ = await call_service.get_property_by_number(db, dialed_number)
        lead_user = None if property_ is not None else await call_service.get_user_by_lead_number(db, dialed_number)

        if property_ is None and lead_user is None:
            logger.warning(
                "No property or lead line configured for dialed number %s, ending call %s",
                dialed_number,
                exotel_call_id,
            )
            if ringing_audio_task is not None:
                ringing_audio_task.cancel()
                try:
                    await ringing_audio_task
                except asyncio.CancelledError:
                    pass
            try:
                await websocket.close()
            except RuntimeError:
                pass  # caller already disconnected
            return

        if property_ is not None:
            host_user_id = property_.user_id
        else:
            host_user_id = lead_user.id
        # Captured as a plain value here (not read again later off property_
        # directly) mainly for readability/reuse across this block --
        # historically also worked around a MissingGreenlet hazard from
        # reading property_.id after a same-session Postgres commit, which
        # no longer applies now that acquire_or_reject touches Redis, not
        # this `db` session, at all.
        busy_recovery_property_id = property_.id if property_ is not None else None

        # CallCoordinator is the single authority on "is this host/property
        # already on a live call" -- see app/services/call_coordinator.py.
        # This pipeline only ever branches on the two-value Decision it
        # returns; it never sees lease internals beyond the Lease dataclass
        # (token/holder_ref/expires_at) -- renewal/release/transfer logic
        # stays entirely inside CallCoordinator. holder_ref is
        # exotel_call_id, the same identifier this call is already keyed by
        # everywhere else (CallSession.exotel_call_id) -- no new identifier
        # invented for this. Redis migration: acquire_or_reject no longer
        # takes `db` -- lease state lives in Redis now, not this Postgres
        # session (see call_coordinator.py's module docstring).
        #
        # Fails open (treated as START_PIPELINE) on any error beyond the
        # "someone else already holds this" case acquire() itself handles --
        # a Redis outage here must never turn away a real guest with no
        # actual conflicting call. Matches this codebase's existing
        # discipline for every other optional/best-effort dependency
        # (SearchApi, SMTP, Twilio all fail open rather than blocking the
        # guest -- see CLAUDE.md/docs/agents.md); unlike those, this failure
        # is logged loudly by call_coordinator.acquire_or_reject itself
        # (lease_redis_unavailable) as an explicit operational signal that
        # busy-call protection is degraded, not silently swallowed.
        try:
            decision, lease = await call_coordinator.acquire_or_reject(
                host_user_id, busy_recovery_property_id, holder_ref=exotel_call_id
            )
        except Exception:
            logger.exception(
                "CallCoordinator.acquire_or_reject failed for host %s, call %s; failing open (call proceeds)",
                host_user_id,
                exotel_call_id,
            )
            decision, lease = call_coordinator.Decision.START_PIPELINE, None
        if decision is call_coordinator.Decision.BUSY_RECOVERY:
            logger.warning(
                "Host/property %s already on a live call; rejecting call %s as BUSY_RECOVERY",
                host_user_id,
                exotel_call_id,
            )
            # Placeholder clip + hang up first -- the rejected caller's own
            # experience (short message, quick disconnect) must never wait
            # on anything below. Reuses the exact same raw-websocket audio
            # machinery as the ringing tone above (app/voice/ringing_audio.py)
            # -- both write Exotel's wire protocol directly because no
            # pipecat transport exists at this point in the call.
            await _reject_call_as_busy(websocket, call_data, ringing_audio_task, exotel_call_id)

            # RecoveryService (app/services/recovery_service.py) is the
            # consumer of BUSY_RECOVERY -- CallCoordinator itself knows
            # nothing about Leads/Notifications/WhatsApp (see its module
            # docstring). NOT fired from here: recorded and handed off to
            # the caller instead, which fires it as a detached task only
            # after `db` (this `async with` block) has fully closed -- see
            # busy_recovery_args' own comment for why firing it from inside
            # this block races the connection pool.
            busy_recovery_args = {
                "host_user_id": host_user_id,
                "property_id": busy_recovery_property_id,
                "caller_number": caller_number,
                "dialed_number": dialed_number,
            }
        else:
            guest = await call_service.get_or_create_guest_profile(db, caller_number, host_user_id)
            active_booking = await lead_service.get_active_booking(db, guest.id if guest else None, host_user_id)

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
                verified_faq_entries = await faq_service.list_verified_property_faq(db, property_.id)
                system_prompt = build_system_prompt(
                    property_, guest, host, active_booking, caller_phone=caller_number,
                    verified_faq_entries=verified_faq_entries,
                )
                first_message = first_message_for(property_, guest, host)
                property_id = property_.id
                property_name = property_.spoken_name or property_.display_name or property_.name
                voice_gender = host.agent_voice_gender
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
                system_prompt = build_lead_system_prompt(
                    lead_user, properties, guest, active_booking, caller_phone=caller_number
                )
                first_message = lead_first_message_for(lead_user)
                property_id = None
                property_name = None
                voice_gender = lead_user.agent_voice_gender

            call_session_id = session.id

    # RecoveryService is fired here -- strictly after `db` (the async with
    # block above) has fully closed, not from inside it (see
    # busy_recovery_args' own comment near the top of this function).
    # Detached, same fire-and-forget pattern as every WhatsApp/email send in
    # tool_handlers.py -- the caller has already been disconnected inside
    # the BUSY_RECOVERY branch above, so nothing here can add latency to
    # their experience.
    if busy_recovery_args is not None:
        asyncio.create_task(recovery_service.handle_busy_recovery(**busy_recovery_args))
        return

    serializer = ExotelFrameSerializer(stream_sid=call_data.stream_id, call_sid=exotel_call_id)
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            vad_analyzer=create_vad_analyzer(_VAD_PARAMS),
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
        guest_profile_id=guest.id if guest else None,
        guest_known_name=guest.name if guest else None,
        exotel_call_id=exotel_call_id,
        ringing_audio_task=ringing_audio_task,
        voice_gender=voice_gender,
        call_lease_token=lease.token if lease is not None else None,
    )


async def run_voice_pipeline_twilio(websocket: WebSocket, call_data: CallData) -> None:
    """Twilio equivalent of run_voice_pipeline above -- deliberately a
    parallel, independent function rather than a shared/refactored one, so
    nothing about the Exotel entry point above is touched. Added purely so
    real-call testing can continue on Twilio's free trial once Exotel
    credits run out (see app/config.py's twilio_voice_webhook_token
    comment for the full reasoning).

    Real differences from run_voice_pipeline, all forced by how Twilio's
    Media Streams protocol actually works (confirmed by reading pipecat's
    parse_telephony_websocket/_create_telephony_transport):
    - call_data.from_number/to_number come from TwiML <Parameter> custom
      stream parameters (see app/integrations/twilio_voice.py's
      build_connect_stream_twiml), not natively from Twilio's own "start"
      event the way Exotel's call_data.from_number/to_number are.
    - TwilioFrameSerializer needs account_sid/auth_token (reused from
      settings.twilio_account_sid/twilio_auth_token -- the same Twilio
      account already configured for WhatsApp), not just the stream/call
      SIDs ExotelFrameSerializer needs.
    - Property/host lookup goes through Property.twilio_number /
      User.twilio_lead_number (call_service.get_property_by_twilio_number /
      get_user_by_twilio_lead_number) -- separate columns from
      Property.exophone / User.lead_exophone, so a host can have both an
      Exotel and a Twilio number configured independently.
    - exotel_call_id is deliberately left unset (None) below -- passing a
      Twilio Call SID into that parameter would both mis-tag
      CallSession.exotel_call_id (a column that means something specific:
      an Exotel call identifier) and cause _run_pipeline_inner's call-ending
      path to wrongly attempt an Exotel hangup API call with a Twilio SID.
      Same as the browser-test pipelines below, which also always pass
      exotel_call_id=None -- a Twilio call simply always creates a fresh
      CallSession (no dedup-by-call-id), which is fine for testing and
      avoids needing any change to call_service.get_or_create_call_session's
      existing (Exotel-specific-by-name) parameter.
    - No explicit hangup-via-API on call end (unlike Exotel's
      exotel_client.hangup_call) -- not added here to avoid touching
      _run_pipeline_inner's Exotel-specific hangup branch. Twilio's own
      <Connect><Stream> TwiML verb already ends the call naturally once this
      websocket closes (there's nothing after <Connect> in the TwiML), so
      this isn't expected to leave calls hanging -- just not yet confirmed
      against a real Twilio call the way the Exotel path has been.

    The ring-tone helper (app/voice/ringing_audio.py) IS reused unchanged
    below -- not a modification, just a call into existing code -- because
    Twilio's own Media Streams outbound frame shape
    ({"event": "media", "streamSid": ..., "media": {"payload": ...}}) is
    byte-identical to Exotel's, confirmed against Twilio's own Media Streams
    documentation.

    CallCoordinator IS wired here too (cleanup-pass fix -- this was missing
    entirely, an undocumented gap, not a deliberate omission like the ones
    above): same acquire_or_reject/BUSY_RECOVERY/RecoveryService shape as
    run_voice_pipeline, with call_sid (Twilio's own call identifier) as
    holder_ref instead of exotel_call_id. Without this, two simultaneous
    Twilio calls to the same host/property would both start a full
    pipeline -- exactly the double-booking scenario Busy Call Recovery
    exists to prevent, silently unprotected on this path only.
    """
    call_sid = call_data.call_id
    dialed_number = call_data.to_number
    caller_number = call_data.from_number

    ringing_audio_task = (
        asyncio.create_task(play_ringing_tone(websocket, call_data.stream_id)) if call_data.stream_id else None
    )

    busy_recovery_args: dict | None = None

    async with AsyncSessionLocal() as db:
        property_ = await call_service.get_property_by_twilio_number(db, dialed_number)
        lead_user = (
            None if property_ is not None else await call_service.get_user_by_twilio_lead_number(db, dialed_number)
        )

        if property_ is None and lead_user is None:
            logger.warning(
                "No property or lead line configured for dialed Twilio number %s, ending call %s",
                dialed_number,
                call_sid,
            )
            if ringing_audio_task is not None:
                ringing_audio_task.cancel()
                try:
                    await ringing_audio_task
                except asyncio.CancelledError:
                    pass
            try:
                await websocket.close()
            except RuntimeError:
                pass  # caller already disconnected
            return

        if property_ is not None:
            host_user_id = property_.user_id
        else:
            host_user_id = lead_user.id
        busy_recovery_property_id = property_.id if property_ is not None else None

        try:
            decision, lease = await call_coordinator.acquire_or_reject(
                host_user_id, busy_recovery_property_id, holder_ref=call_sid
            )
        except Exception:
            logger.exception(
                "CallCoordinator.acquire_or_reject failed for host %s, call %s; failing open (call proceeds)",
                host_user_id,
                call_sid,
            )
            decision, lease = call_coordinator.Decision.START_PIPELINE, None
        if decision is call_coordinator.Decision.BUSY_RECOVERY:
            logger.warning(
                "Host/property %s already on a live call; rejecting Twilio call %s as BUSY_RECOVERY",
                host_user_id,
                call_sid,
            )
            # exotel_call_id=None here (never set on this path) -- same
            # helper run_voice_pipeline (Exotel) uses, correctly skips the
            # Exotel-specific hangup step: Twilio's <Connect><Stream> TwiML
            # ends the call once this websocket closes, there is nothing
            # Exotel-hangup-shaped to call.
            await _reject_call_as_busy(websocket, call_data, ringing_audio_task, exotel_call_id=None)

            busy_recovery_args = {
                "host_user_id": host_user_id,
                "property_id": busy_recovery_property_id,
                "caller_number": caller_number,
                "dialed_number": dialed_number,
            }
        else:
            guest = await call_service.get_or_create_guest_profile(db, caller_number, host_user_id)
            active_booking = await lead_service.get_active_booking(db, guest.id if guest else None, host_user_id)

            if property_ is not None:
                host = await db.get(User, property_.user_id)
                session = await call_service.get_or_create_call_session(
                    db,
                    exotel_call_id=None,
                    property_id=property_.id,
                    guest_profile_id=guest.id if guest else None,
                    caller_number=caller_number,
                    user_id=property_.user_id,
                )
                verified_faq_entries = await faq_service.list_verified_property_faq(db, property_.id)
                system_prompt = build_system_prompt(
                    property_, guest, host, active_booking, caller_phone=caller_number,
                    verified_faq_entries=verified_faq_entries,
                )
                first_message = first_message_for(property_, guest, host)
                property_id = property_.id
                property_name = property_.name
                voice_gender = host.agent_voice_gender
            else:
                properties = list(
                    (await db.scalars(select(Property).where(Property.user_id == lead_user.id))).all()
                )
                session = await call_service.get_or_create_call_session(
                    db,
                    exotel_call_id=None,
                    property_id=None,
                    guest_profile_id=guest.id if guest else None,
                    caller_number=caller_number,
                    user_id=lead_user.id,
                )
                system_prompt = build_lead_system_prompt(
                    lead_user, properties, guest, active_booking, caller_phone=caller_number
                )
                first_message = lead_first_message_for(lead_user)
                property_id = None
                property_name = None
                voice_gender = lead_user.agent_voice_gender

            call_session_id = session.id

    if busy_recovery_args is not None:
        asyncio.create_task(recovery_service.handle_busy_recovery(**busy_recovery_args))
        return

    serializer = TwilioFrameSerializer(
        stream_sid=call_data.stream_id,
        call_sid=call_sid,
        account_sid=settings.twilio_account_sid or "",
        auth_token=settings.twilio_auth_token or "",
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            vad_analyzer=create_vad_analyzer(_VAD_PARAMS),
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
        guest_profile_id=guest.id if guest else None,
        guest_known_name=guest.name if guest else None,
        ringing_audio_task=ringing_audio_task,
        voice_gender=voice_gender,
        call_lease_token=lease.token if lease is not None else None,
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
            db, call_service.BROWSER_TEST_CALLER_NUMBER, property_.user_id, name="Browser test guest"
        )
        session = await call_service.get_or_create_call_session(
            db,
            exotel_call_id=None,
            property_id=property_.id,
            guest_profile_id=guest.id if guest else None,
            caller_number=call_service.BROWSER_TEST_CALLER_NUMBER,
            user_id=property_.user_id,
        )
        verified_faq_entries = await faq_service.list_verified_property_faq(db, property_.id)
        system_prompt = build_system_prompt(property_, None, host, verified_faq_entries=verified_faq_entries)
        first_message = first_message_for(property_, None, host)
        property_id = property_.id
        host_user_id = property_.user_id
        call_session_id = session.id
        guest_profile_id = guest.id if guest else None

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=create_vad_analyzer(_VAD_PARAMS),
        ),
    )

    await _run_pipeline(
        transport,
        property_id,
        call_session_id,
        host_user_id,
        system_prompt,
        first_message,
        property_name=property_.spoken_name or property_.display_name or property_.name,
        guest_profile_id=guest_profile_id,
        voice_gender=host.agent_voice_gender,
    )


async def run_browser_lead_pipeline(connection: SmallWebRTCConnection, user: User) -> None:
    """Same as run_browser_voice_pipeline, but for testing the Lead Agent
    flow across a host's full portfolio instead of one property."""
    async with AsyncSessionLocal() as db:
        properties = list((await db.scalars(select(Property).where(Property.user_id == user.id))).all())
        guest = await call_service.get_or_create_guest_profile(
            db, call_service.BROWSER_TEST_CALLER_NUMBER, user.id, name="Browser test guest"
        )
        active_booking = await lead_service.get_active_booking(db, guest.id if guest else None, user.id)
        session = await call_service.get_or_create_call_session(
            db,
            exotel_call_id=None,
            property_id=None,
            guest_profile_id=guest.id if guest else None,
            caller_number=call_service.BROWSER_TEST_CALLER_NUMBER,
            user_id=user.id,
        )
        system_prompt = build_lead_system_prompt(user, properties, guest, active_booking)
        first_message = lead_first_message_for(user)
        call_session_id = session.id
        guest_profile_id = guest.id if guest else None

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=create_vad_analyzer(_VAD_PARAMS),
        ),
    )

    await _run_pipeline(
        transport,
        None,
        call_session_id,
        user.id,
        system_prompt,
        first_message,
        guest_profile_id=guest_profile_id,
        voice_gender=user.agent_voice_gender,
    )
