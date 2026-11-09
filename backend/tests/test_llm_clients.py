"""Shared LLM clients (one per provider/profile, explicit limits, closed on shutdown) and the
controls that keep post-call LLM work from starving live calls of Groq quota."""

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.integrations import llm_clients
from app.services import call_classification_service as classification
from app.services import call_summary_service as summary


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test")
    monkeypatch.setattr(settings, "openrouter_api_key", "or_test")
    monkeypatch.setattr(settings, "anthropic_api_key", "ant_test")


# ---- client reuse + limits -------------------------------------------------


async def test_same_provider_and_profile_reuses_one_client():
    assert llm_clients.groq_client() is llm_clients.groq_client()
    assert llm_clients.openrouter_client() is llm_clients.openrouter_client()
    assert llm_clients.anthropic_client() is llm_clients.anthropic_client()


async def test_different_profiles_are_different_clients_with_their_own_limits():
    background = llm_clients.groq_client(llm_clients.BACKGROUND)
    probe = llm_clients.groq_client(llm_clients.PROBE)
    assert background is not probe
    assert background.max_retries == llm_clients.BACKGROUND.max_retries == 1
    assert probe.max_retries == 0  # a health ping must be a real measurement, no hidden retries


async def test_clients_never_use_the_sdk_default_ten_minute_timeout():
    for client in (
        llm_clients.groq_client(),
        llm_clients.openrouter_client(),
        llm_clients.anthropic_client(),
        llm_clients.openrouter_client(llm_clients.EMBEDDING),
    ):
        assert isinstance(client.timeout, httpx.Timeout)
        assert client.timeout.read is not None and client.timeout.read <= 120
        assert client.timeout.connect is not None and client.timeout.connect <= 5


async def test_a_separate_key_gets_a_separate_client():
    shared = llm_clients.groq_client()
    other = llm_clients.groq_client(api_key="gsk_other_account")
    assert shared is not other


async def test_the_sdk_class_is_resolved_at_call_time_so_test_fakes_work(monkeypatch):
    import groq

    created = []

    class _Fake:
        def __init__(self, api_key=None, **options):
            created.append(options)

    monkeypatch.setattr(groq, "AsyncGroq", _Fake)
    client = llm_clients.groq_client(llm_clients.DOCUMENT)
    assert isinstance(client, _Fake)
    assert created[0]["max_retries"] == llm_clients.DOCUMENT.max_retries


async def test_aclose_all_closes_every_client_and_forgets_them():
    client = llm_clients.groq_client()
    closed = []
    original_close = client.close

    async def _close():
        closed.append(True)
        await original_close()

    client.close = _close
    await llm_clients.aclose_all()
    assert closed == [True]
    assert llm_clients.groq_client() is not client  # a fresh one is built after shutdown


# ---- post-call quota controls ---------------------------------------------


def test_post_call_model_and_key_default_to_the_live_ones(monkeypatch):
    monkeypatch.setattr(settings, "post_call_groq_model", None)
    monkeypatch.setattr(settings, "post_call_groq_api_key", None)
    assert llm_clients.post_call_groq_model() == settings.groq_model


async def test_post_call_can_use_its_own_model_and_key(monkeypatch):
    monkeypatch.setattr(settings, "post_call_groq_model", "openai/gpt-oss-20b")
    monkeypatch.setattr(settings, "post_call_groq_api_key", "gsk_post_call_account")
    assert llm_clients.post_call_groq_model() == "openai/gpt-oss-20b"
    assert llm_clients.post_call_groq_client() is not llm_clients.groq_client()  # a different key


async def test_post_call_slot_caps_how_many_run_at_once(monkeypatch):
    monkeypatch.setattr(settings, "post_call_llm_max_concurrency", 2)
    llm_clients._post_call_slots.clear()
    running = peak = 0

    async def job():
        nonlocal running, peak
        async with llm_clients.post_call_slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1

    await asyncio.gather(*(job() for _ in range(8)))
    assert peak == 2


# ---- classification / summary behaviour -----------------------------------

_VALID_CLASSIFICATION = json.dumps({"call_type": "BOOKING_LEAD", "confidence": 0.9, "reason": "asked for dates"})
_TRANSCRIPT = "user: hi I want to book a villa in Goa for 3 nights\nassistant: sure, which dates?\nuser: next friday"


async def test_a_failed_classification_is_flagged_so_nothing_acts_on_it(monkeypatch):
    async def _boom(transcript):
        raise RuntimeError("groq 429")

    monkeypatch.setattr(classification, "_call_llm_with_fallback", _boom)
    result = await classification.classify_call(_TRANSCRIPT, 120)
    assert result.call_type == "UNKNOWN"
    assert result.classification_failed is True


async def test_a_genuine_classification_is_not_flagged(monkeypatch):
    async def _ok(transcript):
        return _VALID_CLASSIFICATION

    monkeypatch.setattr(classification, "_call_llm_with_fallback", _ok)
    result = await classification.classify_call(_TRANSCRIPT, 120)
    assert result.call_type == "BOOKING_LEAD"
    assert result.classification_failed is False


async def test_groq_failure_falls_back_to_openrouter_instead_of_failing(monkeypatch):
    async def _groq_429(prompt):
        raise RuntimeError("429 rate limited")

    async def _openrouter(prompt):
        return _VALID_CLASSIFICATION

    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(classification, "_call_groq", _groq_429)
    monkeypatch.setattr(classification, "_call_openrouter", _openrouter)
    assert await classification._call_llm_with_fallback(_TRANSCRIPT) == _VALID_CLASSIFICATION

    monkeypatch.setattr(summary, "_call_groq", _groq_429)
    monkeypatch.setattr(summary, "_call_openrouter", _openrouter)
    assert await summary._call_llm_with_fallback(_TRANSCRIPT) == _VALID_CLASSIFICATION


async def test_groq_failure_without_another_provider_still_raises(monkeypatch):
    async def _groq_429(prompt):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(classification, "_call_groq", _groq_429)
    with pytest.raises(RuntimeError):
        await classification._call_llm_with_fallback(_TRANSCRIPT)


async def test_time_queued_for_a_slot_does_not_count_against_the_request_timeout(monkeypatch):
    """The slot is taken before wait_for starts, so a burst of calls ending together cannot turn
    queueing into spurious UNKNOWN results."""
    monkeypatch.setattr(settings, "post_call_llm_max_concurrency", 1)
    llm_clients._post_call_slots.clear()
    timeouts_started = []
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(awaitable, timeout):
        timeouts_started.append(timeout)
        return await real_wait_for(awaitable, timeout)

    async def _ok(transcript):
        return _VALID_CLASSIFICATION

    monkeypatch.setattr(classification, "_call_llm_with_fallback", _ok)
    monkeypatch.setattr(classification.asyncio, "wait_for", _spy_wait_for)

    async with llm_clients.post_call_slot():  # another post-call request holds the only slot
        pending = asyncio.create_task(classification.classify_call(_TRANSCRIPT, 120))
        await asyncio.sleep(0.1)
        assert timeouts_started == []  # still queued: its timeout clock has not started
    result = await pending
    assert timeouts_started == [15]
    assert result.call_type == "BOOKING_LEAD"
