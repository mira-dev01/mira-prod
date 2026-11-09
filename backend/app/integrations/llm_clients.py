"""Shared, long-lived LLM SDK clients with explicit time limits.

Before this, ~8 services built a brand-new AsyncGroq / AsyncOpenAI /
AsyncAnthropic on EVERY request: no connection reuse (a TLS handshake each
time), the SDKs' default 600-second timeout (a stalled provider held a request
for ten minutes) and 2 automatic retries (a hidden multiplier on latency and
rate-limit pressure), and the clients were never closed.

Now: one client per provider (per profile) per process, built lazily, with the
limits spelled out in a Profile, and closed on shutdown (aclose_all, wired in
app.main's lifespan).

Clients are keyed by the running event loop: an httpx connection pool is bound
to the loop it first ran on, so a client created under one loop must not be
reused under another (this is what keeps tests -- one loop each -- safe, and in
production there is exactly one loop, hence exactly one client per profile).
The SDK class is resolved at call time, not import time, so a test that
monkeypatches `groq.AsyncGroq` still gets its fake.

This module is for BACKGROUND / non-live-call LLM use (post-call
classification and summary, document parsing, health pings, embeddings). The
live voice LLM keeps its own client (app/voice/pipeline.py), tuned for
first-chunk latency and failover.
"""

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Profile:
    """Time and retry limits for one kind of caller."""

    timeout_seconds: float
    connect_seconds: float
    max_retries: int

    def httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_seconds, connect=self.connect_seconds)


# Post-call classification / summary and import-time normalisation: one short
# prompt, callers also wrap the call in their own asyncio.wait_for (15-20s).
BACKGROUND = Profile(timeout_seconds=25.0, connect_seconds=5.0, max_retries=1)
# Policy-document and negotiation-policy parsing: big prompts, big replies;
# callers wrap in asyncio.wait_for(policy_documents.LLM_TIMEOUT_SECONDS=90).
DOCUMENT = Profile(timeout_seconds=100.0, connect_seconds=5.0, max_retries=1)
# Health pings: a real measurement of the route, so no hidden retries.
PROBE = Profile(timeout_seconds=15.0, connect_seconds=5.0, max_retries=0)
# Embeddings: the live-call retrieval path bounds itself with its own deadline.
EMBEDDING = Profile(timeout_seconds=10.0, connect_seconds=3.0, max_retries=0)

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple, object]]" = weakref.WeakKeyDictionary()


def _cached(key: tuple, build):
    per_loop = _clients.setdefault(asyncio.get_running_loop(), {})
    client = per_loop.get(key)
    if client is None:
        client = per_loop[key] = build()
    return client


def groq_client(profile: Profile = BACKGROUND, *, api_key: str | None = None):
    import groq

    key = api_key or settings.groq_api_key
    return _cached(
        ("groq", groq.AsyncGroq, key, profile),
        lambda: groq.AsyncGroq(api_key=key, timeout=profile.httpx_timeout(), max_retries=profile.max_retries),
    )


def anthropic_client(profile: Profile = BACKGROUND):
    import anthropic

    return _cached(
        ("anthropic", anthropic.AsyncAnthropic, settings.anthropic_api_key, profile),
        lambda: anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=profile.httpx_timeout(), max_retries=profile.max_retries
        ),
    )


def openrouter_client(profile: Profile = BACKGROUND):
    import openai

    return _cached(
        ("openrouter", openai.AsyncOpenAI, settings.openrouter_api_key, profile),
        lambda: openai.AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=profile.httpx_timeout(),
            max_retries=profile.max_retries,
        ),
    )


async def aclose_all() -> None:
    """Closes this loop's clients. Called once at application shutdown."""
    per_loop = _clients.pop(asyncio.get_running_loop(), {})
    for client in per_loop.values():
        close = getattr(client, "close", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:
            logger.debug("Ignoring error while closing an LLM client", exc_info=True)


# ---- post-call work must not starve live calls of Groq quota -----------------
#
# Classification and summary used to share the live voice LLM's model AND key,
# so a burst of calls ending together (each sends two big transcript prompts)
# drew down the same per-model rate limit a call still in progress was using
# -- showing up as a 429 on a LIVE call. Three independent controls:
#   1. a concurrency cap on post-call LLM calls (always on);
#   2. an optional separate model (POST_CALL_GROQ_MODEL): Groq limits are
#      per model, so a different model is a separate bucket;
#   3. an optional separate key (POST_CALL_GROQ_API_KEY): a separate account/org
#      is a separate quota entirely.
# (2) and (3) are off by default: choosing a smaller model for summaries is a
# quality trade-off for you to make, not for a code default to make silently.

_post_call_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


@asynccontextmanager
async def post_call_slot():
    """Bounds how many post-call LLM requests run at once in this process."""
    loop = asyncio.get_running_loop()
    semaphore = _post_call_slots.get(loop)
    if semaphore is None:
        semaphore = _post_call_slots[loop] = asyncio.Semaphore(max(1, settings.post_call_llm_max_concurrency))
    async with semaphore:
        yield


def post_call_groq_client(profile: Profile = BACKGROUND):
    return groq_client(profile, api_key=settings.post_call_groq_api_key or settings.groq_api_key)


def post_call_groq_model() -> str:
    return settings.post_call_groq_model or settings.groq_model
