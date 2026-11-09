"""_FallbackGroqLLMService fails over to the next model on a provider 5xx,
a dropped connection, a timeout, or a stalled first token -- not just on a
429 -- so the guest isn't left in dead air."""

import asyncio

import httpx
import openai
import pytest
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta
from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.groq.llm import GroqLLMService
from pipecat.tests.utils import run_test

from app.config import settings
from app.main import llm_health
from app.voice import pipeline

_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _status_error(cls, code):
    return cls("boom", response=httpx.Response(code, request=_REQ), body=None)


class _Stream:
    """Minimal stand-in for openai's AsyncStream."""

    def __init__(self, chunks, delay=0.0):
        self._chunks = list(chunks)
        self._delay = delay
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        for chunk in self._chunks:
            yield chunk

    async def close(self):
        self.closed = True


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(settings, "groq_models", ["m1", "m2", "m3"])
    monkeypatch.setattr(settings, "llm_first_chunk_timeout_seconds", 0.1)
    for model in ("m1", "m2", "m3"):
        llm_health.pop(model, None)
    svc = pipeline._FallbackGroqLLMService(api_key="x", settings=GroqLLMService.Settings(model="m1"))
    svc.pushed = []

    async def _record(frame, direction=None):
        svc.pushed.append(frame)

    monkeypatch.setattr(svc, "push_frame", _record)
    yield svc
    for model in ("m1", "m2", "m3"):
        llm_health.pop(model, None)


def _script(monkeypatch, behaviors):
    """behaviors: model -> exception to raise, or a _Stream to return."""
    calls: list[str] = []

    async def fake(self, context):
        model = self._settings.model
        calls.append(model)
        outcome = behaviors[model]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(GroqLLMService, "get_chat_completions", fake)
    return calls


async def _collect(stream):
    return [chunk async for chunk in stream]


async def test_healthy_first_model_streams_every_chunk_in_order(service, monkeypatch):
    calls = _script(monkeypatch, {"m1": _Stream(["a", "b", "c"])})
    result = await service.get_chat_completions(object())
    assert await _collect(result) == ["a", "b", "c"]
    assert calls == ["m1"]


@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.InternalServerError, 500),
        _status_error(openai.InternalServerError, 503),
        _status_error(openai.RateLimitError, 429),
        _status_error(openai.NotFoundError, 404),
        openai.APIConnectionError(request=_REQ),
        openai.APITimeoutError(request=_REQ),
    ],
    ids=["500", "503", "429", "404", "connection", "sdk-timeout"],
)
async def test_provider_failures_fail_over_to_next_model(service, monkeypatch, error):
    calls = _script(monkeypatch, {"m1": error, "m2": _Stream(["ok"])})
    result = await service.get_chat_completions(object())
    assert await _collect(result) == ["ok"]
    assert calls == ["m1", "m2"]


@pytest.mark.parametrize(
    "error, marked_down",
    [
        (_status_error(openai.RateLimitError, 429), True),
        (_status_error(openai.NotFoundError, 404), True),
        (_status_error(openai.InternalServerError, 500), False),
        (openai.APIConnectionError(request=_REQ), False),
        (openai.APITimeoutError(request=_REQ), False),
    ],
    ids=["429", "404", "500", "connection", "sdk-timeout"],
)
async def test_only_rate_limit_or_retired_model_marks_it_down_for_other_calls(
    service, monkeypatch, error, marked_down
):
    _script(monkeypatch, {"m1": error, "m2": _Stream(["ok"])})
    await service.get_chat_completions(object())
    assert ("m1" in llm_health and llm_health["m1"]["ok"] is False) is marked_down


async def test_a_slow_first_token_never_marks_the_model_down(service, monkeypatch):
    _script(monkeypatch, {"m1": _Stream(["never"], delay=5), "m2": _Stream(["ok"])})
    await service.get_chat_completions(object())
    assert "m1" not in llm_health


async def test_stalled_first_token_fails_over_and_closes_the_stalled_stream(service, monkeypatch):
    stalled = _Stream(["never"], delay=5)
    calls = _script(monkeypatch, {"m1": stalled, "m2": _Stream(["ok"])})
    result = await asyncio.wait_for(service.get_chat_completions(object()), timeout=2)
    assert await _collect(result) == ["ok"]
    assert calls == ["m1", "m2"]
    assert stalled.closed is True


async def test_last_model_gets_a_longer_leash_than_the_others(service, monkeypatch):
    _script(
        monkeypatch,
        {
            "m1": _status_error(openai.InternalServerError, 500),
            "m2": _status_error(openai.InternalServerError, 500),
            "m3": _Stream(["slow but alive"], delay=0.2),  # > 0.1s, < 0.3s
        },
    )
    result = await service.get_chat_completions(object())
    assert await _collect(result) == ["slow but alive"]


@pytest.mark.parametrize("code", [400, 401, 403])
async def test_non_provider_4xx_is_not_retried(service, monkeypatch, code):
    cls = {400: openai.BadRequestError, 401: openai.AuthenticationError, 403: openai.PermissionDeniedError}[code]
    calls = _script(monkeypatch, {"m1": _status_error(cls, code), "m2": _Stream(["ok"])})
    with pytest.raises(cls):
        await service.get_chat_completions(object())
    assert calls == ["m1"]


async def test_every_model_failing_raises_the_last_error(service, monkeypatch):
    calls = _script(
        monkeypatch,
        {
            "m1": _status_error(openai.InternalServerError, 500),
            "m2": openai.APIConnectionError(request=_REQ),
            "m3": _status_error(openai.InternalServerError, 502),
        },
    )
    with pytest.raises(openai.InternalServerError):
        await service.get_chat_completions(object())
    assert calls == ["m1", "m2", "m3"]


async def test_empty_stream_is_returned_not_treated_as_a_failure(service, monkeypatch):
    calls = _script(monkeypatch, {"m1": _Stream([])})
    result = await service.get_chat_completions(object())
    assert await _collect(result) == []
    assert calls == ["m1"]


async def test_client_has_a_bounded_timeout_and_no_sdk_retries(service):
    client = service.create_client(api_key="x", base_url="https://api.groq.com/openai/v1")
    assert client.max_retries == 0
    assert client.timeout.connect == 3.0
    assert client.timeout.read == 10.0


# ---- the "one moment" filler, and the guest never being left in silence ----


def _spoken(svc):
    return [f for f in svc.pushed if isinstance(f, TTSSpeakFrame)]


async def test_failover_speaks_one_filler_that_stays_out_of_the_llm_context(service, monkeypatch):
    _script(monkeypatch, {"m1": _status_error(openai.InternalServerError, 500), "m2": _Stream(["ok"])})
    await service.get_chat_completions(object())
    [filler] = _spoken(service)
    assert filler.text in pipeline._LLM_FAILOVER_FILLER_TEXTS
    # Not recorded in the conversation: the model's reply must read as a plain
    # continuation of what the guest asked, not a response to "one moment".
    assert filler.append_to_context is False


async def test_two_failed_models_still_only_one_filler_per_turn(service, monkeypatch):
    _script(
        monkeypatch,
        {
            "m1": _status_error(openai.InternalServerError, 500),
            "m2": openai.APIConnectionError(request=_REQ),
            "m3": _Stream(["ok"]),
        },
    )
    await service.get_chat_completions(object())
    assert len(_spoken(service)) == 1


async def test_healthy_turn_has_no_filler_and_never_touches_the_watchdog(service, monkeypatch):
    events = []
    service.set_reply_gap_hooks(
        hold_nudges=lambda: _record_async(events, "hold"), release_nudges=lambda: events.append("release")
    )
    _script(monkeypatch, {"m1": _Stream(["ok"])})
    await service.get_chat_completions(object())
    assert _spoken(service) == []
    assert events == []


async def _record_async(events, name):
    events.append(name)


async def test_watchdog_is_held_before_the_filler_and_released_once_a_reply_is_obtained(service, monkeypatch):
    events = []
    service.set_reply_gap_hooks(
        hold_nudges=lambda: _record_async(events, "hold"), release_nudges=lambda: events.append("release")
    )
    original_push = service.push_frame

    async def _push(frame, direction=None):
        events.append("filler")
        await original_push(frame, direction)

    monkeypatch.setattr(service, "push_frame", _push)
    _script(monkeypatch, {"m1": _status_error(openai.InternalServerError, 500), "m2": _Stream(["ok"])})

    await service.get_chat_completions(object())

    assert events == ["hold", "filler", "release"]


async def test_filler_rotates_between_failovers_in_one_call(service, monkeypatch):
    _script(monkeypatch, {"m1": _status_error(openai.InternalServerError, 500), "m2": _Stream(["ok"])})
    await service.get_chat_completions(object())
    # The service sticks to the last model that worked; start the second turn
    # from the failing one again to force another failover.
    service._settings.model = "m1"
    await service.get_chat_completions(object())
    texts = [f.text for f in _spoken(service)]
    assert len(texts) == 2 and texts[0] != texts[1]


async def test_total_failure_hands_the_turn_back_out_loud_and_releases_the_hold(service, monkeypatch):
    events = []
    service.set_reply_gap_hooks(
        hold_nudges=lambda: _record_async(events, "hold"), release_nudges=lambda: events.append("release")
    )
    _script(
        monkeypatch,
        {
            "m1": _status_error(openai.InternalServerError, 500),
            "m2": _status_error(openai.InternalServerError, 500),
            "m3": _status_error(openai.InternalServerError, 502),
        },
    )
    with pytest.raises(openai.InternalServerError):
        await service.get_chat_completions(object())

    texts = [f.text for f in _spoken(service)]
    assert texts[0] in pipeline._LLM_FAILOVER_FILLER_TEXTS
    assert texts[-1] == pipeline._LLM_TOTAL_FAILURE_TEXT
    assert "say that once more" in texts[-1]
    assert all(f.append_to_context is False for f in _spoken(service))
    assert events == ["hold", "release"]  # released: the apology's own end re-arms the normal clock


async def test_non_retryable_error_apologises_without_a_filler(service, monkeypatch):
    _script(monkeypatch, {"m1": _status_error(openai.BadRequestError, 400)})
    with pytest.raises(openai.BadRequestError):
        await service.get_chat_completions(object())
    assert [f.text for f in _spoken(service)] == [pipeline._LLM_TOTAL_FAILURE_TEXT]


async def test_guest_interrupting_mid_failover_releases_the_hold_and_does_not_apologise(service, monkeypatch):
    events = []
    service.set_reply_gap_hooks(
        hold_nudges=lambda: _record_async(events, "hold"), release_nudges=lambda: events.append("release")
    )
    _script(
        monkeypatch,
        {"m1": _status_error(openai.InternalServerError, 500), "m2": _Stream(["never"], delay=30)},
    )
    monkeypatch.setattr(settings, "llm_first_chunk_timeout_seconds", 60.0)

    task = asyncio.create_task(service.get_chat_completions(object()))
    await asyncio.sleep(0.2)  # filler spoken, retry in flight
    task.cancel()  # pipecat cancels the LLM task when the guest barges in
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["hold", "release"]
    assert [f.text for f in _spoken(service)] != [pipeline._LLM_TOTAL_FAILURE_TEXT]
    assert pipeline._LLM_TOTAL_FAILURE_TEXT not in [f.text for f in _spoken(service)]


def _chunk(text):
    return ChatCompletionChunk(
        id="c",
        created=0,
        model="m",
        object="chat.completion.chunk",
        choices=[Choice(index=0, delta=ChoiceDelta(content=text, role="assistant"))],
    )


async def test_real_pipeline_frame_order_filler_then_the_retried_reply(monkeypatch):
    """Through pipecat's real LLM service plumbing (not a mocked push_frame):
    the filler is spoken inside the turn, then the second model's reply
    streams out as normal text, then the turn ends -- same flow, no gap."""
    monkeypatch.setattr(settings, "groq_models", ["m1", "m2"])
    svc = pipeline._FallbackGroqLLMService(api_key="x", settings=GroqLLMService.Settings(model="m1"))

    async def _fake(self, context):
        if self._settings.model == "m1":
            raise _status_error(openai.InternalServerError, 500)
        return _Stream([_chunk("Sure, "), _chunk("it is 4000 rupees.")])

    monkeypatch.setattr(GroqLLMService, "get_chat_completions", _fake)
    context = LLMContext(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "price?"}])

    down, _ = await run_test(svc, frames_to_send=[LLMContextFrame(context=context)])

    kinds = [type(f) for f in down if not type(f).__name__.endswith("MetadataFrame")]
    assert kinds == [
        LLMFullResponseStartFrame,
        TTSSpeakFrame,
        LLMTextFrame,
        LLMTextFrame,
        LLMFullResponseEndFrame,
    ]
    assert "".join(f.text for f in down if isinstance(f, LLMTextFrame)) == "Sure, it is 4000 rupees."
    # The conversation the model sees is untouched by the filler.
    assert [m["role"] for m in context.messages] == ["system", "user"]


async def _run_slow_retry_with_watchdog(monkeypatch, *, wire_hooks: bool):
    """Real SilenceWatchdogProcessor + real failover service in one pipeline.
    m1 fails instantly, m2 answers after 0.5s -- five watchdog timeouts (0.1s)
    long. The filler's bot-stopped event lands mid-retry (t=0.15s)."""
    from pipecat.frames.frames import BotStoppedSpeakingFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.tests.utils import SleepFrame

    from app.voice.silence_watchdog import SilenceWatchdogProcessor

    monkeypatch.setattr(settings, "groq_models", ["m1", "m2"])
    monkeypatch.setattr(settings, "llm_first_chunk_timeout_seconds", 5.0)
    svc = pipeline._FallbackGroqLLMService(api_key="x", settings=GroqLLMService.Settings(model="m1"))
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    if wire_hooks:
        svc.set_reply_gap_hooks(hold_nudges=watchdog.hold_nudges, release_nudges=watchdog.release_nudges)

    async def _fake(self, context):
        if self._settings.model == "m1":
            raise _status_error(openai.InternalServerError, 500)
        await asyncio.sleep(0.5)
        return _Stream([_chunk("It is 4000 rupees.")])

    monkeypatch.setattr(GroqLLMService, "get_chat_completions", _fake)
    context = LLMContext(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "price?"}])

    down, _ = await run_test(
        Pipeline([watchdog, svc]),
        frames_to_send=[
            LLMContextFrame(context=context),
            SleepFrame(sleep=0.15),
            BotStoppedSpeakingFrame(),  # the filler finished playing
            SleepFrame(sleep=0.8),
        ],
    )
    return down


async def test_slow_retry_never_makes_mira_ask_are_you_there(monkeypatch):
    down = await _run_slow_retry_with_watchdog(monkeypatch, wire_hooks=True)
    spoken = [f.text for f in down if isinstance(f, TTSSpeakFrame)]
    assert spoken == [spoken[0]] and spoken[0] in pipeline._LLM_FAILOVER_FILLER_TEXTS  # filler only
    assert "".join(f.text for f in down if isinstance(f, LLMTextFrame)) == "It is 4000 rupees."


async def test_control_without_the_hold_the_watchdog_would_have_nudged(monkeypatch):
    """Proves the test above has teeth: same scenario, hooks not wired."""
    down = await _run_slow_retry_with_watchdog(monkeypatch, wire_hooks=False)
    assert "Hello?" in [f.text for f in down if isinstance(f, TTSSpeakFrame)]
