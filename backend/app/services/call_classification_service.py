"""Classifies a finished call's transcript into the CallType taxonomy
(schemas/call_classification.py). The single centralized place this decision
is made -- app/voice/pipeline.py's on_pipeline_finished calls this, persists
the result via call_service.set_call_classification, and gates Lead
visibility on it via lead_service.delete_for_unqualified_call. To add a new
category later: add the literal to CallType, decide qualified/not, and add
its definition to _CLASSIFICATION_PROMPT below -- nothing else in the
codebase needs to change.

Deliberately NOT built on app/voice/pipeline.py's _build_llm()/pipecat
services, for the same reason app/services/discount_policy_service.py isn't:
those are wired for the streaming, function-calling voice pipeline and
aren't a fit for a single one-shot JSON-extraction call after the call has
already ended. This copies discount_policy_service.py's pattern -- the same
provider SDKs directly, one-model-then-fallback -- since a low-frequency,
per-call classification call needs exactly the same shape of thing.
"""

import asyncio
import json
import logging

from app.config import settings
from app.schemas.call_classification import CallType, ClassificationResult

logger = logging.getLogger(__name__)

MIN_MEANINGFUL_DURATION_SECONDS = 4.0

_VALID_CALL_TYPES: set[str] = {
    "BOOKING_LEAD",
    "GUEST_SUPPORT",
    "EXISTING_BOOKING",
    "GENERAL_QUERY",
    "JUNK",
    "INCOMPLETE",
    "UNKNOWN",
}

_CLASSIFICATION_PROMPT = """You classify a finished phone call to a short-term rental host's AI receptionist, \
based on its transcript. Read the transcript and choose exactly one category:

- BOOKING_LEAD: the caller is asking about availability, dates, or pricing with real intent to possibly stay, \
or is negotiating/discussing a new booking (e.g. "Do you have anything free next weekend, how much would it be?").
- GUEST_SUPPORT: an existing/current guest reporting an issue or asking a support question about their ongoing \
stay (e.g. maintenance, wifi, check-in/check-out logistics, late arrival, airport pickup).
- EXISTING_BOOKING: the caller is referencing, modifying, confirming, or asking about a booking they already \
have (e.g. "I wanted to change my check-in date" or "just confirming my reservation for next month").
- GENERAL_QUERY: a legitimate question that isn't a new booking intent or a support issue for an existing stay \
-- e.g. general property info, house policies, local recommendations, FAQs.
- JUNK: telemarketing, spam, robocalls, wrong numbers, scam attempts, or a call with noise/silence and no real \
speech or intent from a caller at all.
- INCOMPLETE: a real attempted conversation that got cut off before any purpose became clear -- distinguish \
from JUNK by whether there was genuine caller intent visible, just truncated, versus no genuine intent ever \
present.

Respond with ONLY a JSON object of this exact shape, no other text:
{"call_type": "ONE_OF_THE_ABOVE", "confidence": 0.0, "reason": "one short sentence"}

Call transcript:
"""


class CallClassificationError(Exception):
    pass


def _pre_check(transcript: str | None, duration_seconds: float | None) -> ClassificationResult | None:
    """Cheap short-circuit for calls with no real content to classify --
    not a reintroduction of rule-based classification, just avoiding a
    pointless LLM call when there's nothing to read."""
    if not (transcript or "").strip():
        return ClassificationResult(call_type="INCOMPLETE", confidence=1.0, reason="No speech captured during the call.")
    if duration_seconds is not None and duration_seconds < MIN_MEANINGFUL_DURATION_SECONDS:
        return ClassificationResult(
            call_type="INCOMPLETE", confidence=1.0, reason=f"Call lasted only {duration_seconds:.1f}s."
        )
    return None


def _parse_classification_response(raw_text: str) -> ClassificationResult:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CallClassificationError(f"Model did not return valid JSON: {exc}") from exc

    call_type = parsed.get("call_type")
    if not isinstance(call_type, str):
        raise CallClassificationError("Model response missing a 'call_type' string")
    call_type = call_type.strip().upper()
    if call_type not in _VALID_CALL_TYPES:
        raise CallClassificationError(f"Model returned an unrecognized call_type: {call_type!r}")

    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))

    reason = parsed.get("reason")
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else "No reason given."

    return ClassificationResult(call_type=call_type, confidence=confidence, reason=reason)  # type: ignore[arg-type]


async def _call_groq(prompt: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


async def _call_anthropic(prompt: str) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


async def _call_openrouter(prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


async def _call_llm_with_fallback(transcript: str) -> str:
    prompt = _CLASSIFICATION_PROMPT + transcript

    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return await _call_anthropic(prompt)
    if settings.groq_api_key:
        return await _call_groq(prompt)
    if settings.openrouter_api_key:
        return await _call_openrouter(prompt)
    raise CallClassificationError("No LLM provider is configured (GROQ_API_KEY/ANTHROPIC_API_KEY/OPENROUTER_API_KEY)")


async def classify_call(transcript: str | None, duration_seconds: float | None) -> ClassificationResult:
    """Pure function of the transcript + duration -- no DB access, callers
    persist the result themselves (see call_service.set_call_classification).
    Never raises: any pre-check miss that fails at the LLM step (bad
    response, timeout, no provider configured) degrades to UNKNOWN rather
    than propagating, since this must never crash on_pipeline_finished."""
    pre = _pre_check(transcript, duration_seconds)
    if pre is not None:
        return pre

    try:
        raw = await asyncio.wait_for(_call_llm_with_fallback(transcript or ""), timeout=15)
        return _parse_classification_response(raw)
    except Exception:
        logger.exception("Call classification failed")
        return ClassificationResult(call_type="UNKNOWN", confidence=0.0, reason="Classification failed.")
