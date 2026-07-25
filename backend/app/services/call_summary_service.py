"""Generates the structured, host-facing end-of-call summary
(schemas/call_summary.py) from a finished call's transcript --
app/voice/pipeline.py's on_pipeline_finished calls this alongside
call_classification_service.classify_call and persists the result via
call_service.set_call_summary.

Deliberately NOT built on app/voice/pipeline.py's _build_llm()/pipecat
services, for the same reason call_classification_service.py isn't: those are
wired for the streaming, function-calling voice pipeline and aren't a fit for
a single one-shot JSON-extraction call after the call has already ended. This
copies that file's pattern -- the same provider SDKs directly,
one-model-then-fallback -- since a low-frequency, per-call summary call needs
exactly the same shape of thing.
"""

import asyncio
import json
import logging

from app.config import settings
from app.schemas.call_summary import BookingSnapshot, CallSummary, SummaryOutcome

logger = logging.getLogger(__name__)

MIN_MEANINGFUL_DURATION_SECONDS = 4.0

_NO_CONTENT_SUMMARY = CallSummary(
    booking_snapshot=BookingSnapshot(),
    conversation_summary="Call ended before any conversation took place.",
    outcome=SummaryOutcome(status="No Booking", reason="No speech was captured during the call."),
    host_action=["No action required."],
    key_details=[],
    missing_information=["Check-in date", "Check-out date", "Number of guests", "Budget", "Contact details"],
)

_FAILED_SUMMARY = CallSummary(
    booking_snapshot=BookingSnapshot(),
    conversation_summary="Summary generation failed for this call.",
    outcome=SummaryOutcome(status="No Booking", reason="Summary could not be generated."),
    host_action=["Review the transcript manually."],
    key_details=[],
    missing_information=[],
)

# Kept in strict lockstep with schemas/call_summary.py's field names and
# defaults -- if you add/rename a field there, update both the prompt's shape
# and _parse_summary_response below in the same change.
_SUMMARY_PROMPT = """You write a structured, host-facing summary of a finished phone call to a short-term \
rental host's AI receptionist ("Mira"), based on its transcript. The host must be able to understand the \
entire call in under 10 seconds without reading the transcript.

Do NOT hallucinate. If information is absent, explicitly write "Not mentioned" (or "Not provided" for dates, \
or "Unknown" for counts) -- never infer dates, guest counts, or booking intent that isn't stated. Only use \
facts present in the transcript. Be extremely concise, professional, and scan-friendly -- no marketing \
language, no filler, no repetition. conversation_summary must be 3-5 sentences covering: why the guest \
called, what information the guest shared, what Mira asked, what information Mira provided, any important \
preferences/constraints, and how the conversation ended.

If the call is spam/telemarketing/wrong-number: set outcome.status to "Spam / Junk Call" and explain why in \
outcome.reason. If the guest disconnects early, say so in conversation_summary. If Mira transfers/escalates \
the call, mention the reason. If multiple properties or dates are discussed, list/mention all of them.

Respond with ONLY a JSON object of this exact shape, no other text:
{
  "booking_snapshot": {
    "guest_name": "Name or Unknown",
    "intent": "New Booking | Existing Booking | Inquiry | Support | Complaint | Wrong Number | Spam | Other",
    "check_in": "date or Not provided",
    "check_out": "date or Not provided",
    "nights": "number or Unknown",
    "guests": "number or Unknown",
    "property": ["property names suggested/discussed, or empty list if not discussed"],
    "budget": "budget if mentioned, else Not mentioned",
    "occasion": "e.g. Birthday, Family Trip, Work, Honeymoon, Friends, or Not mentioned",
    "language": "e.g. English, Hindi, Hinglish, Bengali",
    "room_number": "room/unit number the guest stated for an in-stay request, else Not mentioned"
  },
  "conversation_summary": "3-5 sentence summary as described above",
  "outcome": {
    "status": "Booking Confirmed | Booking Likely | Awaiting Guest Details | Awaiting Host Action | Follow-up Required | Guest Dropped | No Booking | Spam / Junk Call",
    "reason": "one sentence explaining why"
  },
  "host_action": ["only items requiring the host's attention, or [\\"No action required.\\"]"],
  "key_details": ["factual bullet points only, e.g. group size, preferences, city of origin, special requests"],
  "missing_information": ["booking info Mira could not collect, e.g. Check-in date, Budget -- or [] if none missing"]
}

Call transcript:
"""


class CallSummaryError(Exception):
    pass


def _pre_check(transcript: str | None, duration_seconds: float | None) -> CallSummary | None:
    """Cheap short-circuit for calls with no real content to summarize --
    avoids a pointless LLM call when there's nothing to read."""
    if not (transcript or "").strip():
        return _NO_CONTENT_SUMMARY
    if duration_seconds is not None and duration_seconds < MIN_MEANINGFUL_DURATION_SECONDS:
        return CallSummary(
            booking_snapshot=BookingSnapshot(),
            conversation_summary=f"Call lasted only {duration_seconds:.1f}s -- too short for a real conversation.",
            outcome=SummaryOutcome(
                status="No Booking", reason=f"Call lasted only {duration_seconds:.1f}s, too short to capture intent."
            ),
            host_action=["No action required."],
            key_details=[],
            missing_information=[],
        )
    return None


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_summary_response(raw_text: str) -> CallSummary:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CallSummaryError(f"Model did not return valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise CallSummaryError("Model response was not a JSON object")

    snapshot_raw = parsed.get("booking_snapshot")
    snapshot = snapshot_raw if isinstance(snapshot_raw, dict) else {}
    booking_snapshot = BookingSnapshot(
        guest_name=str(snapshot.get("guest_name") or "Unknown"),
        intent=str(snapshot.get("intent") or "Other"),
        check_in=str(snapshot.get("check_in") or "Not provided"),
        check_out=str(snapshot.get("check_out") or "Not provided"),
        nights=str(snapshot.get("nights") or "Unknown"),
        guests=str(snapshot.get("guests") or "Unknown"),
        property=_as_str_list(snapshot.get("property")),
        budget=str(snapshot.get("budget") or "Not mentioned"),
        occasion=str(snapshot.get("occasion") or "Not mentioned"),
        language=str(snapshot.get("language") or "Not mentioned"),
        room_number=str(snapshot.get("room_number") or "Not mentioned"),
    )

    conversation_summary = parsed.get("conversation_summary")
    conversation_summary = (
        conversation_summary.strip()
        if isinstance(conversation_summary, str) and conversation_summary.strip()
        else "No summary could be generated for this call."
    )

    outcome_raw = parsed.get("outcome")
    outcome_raw = outcome_raw if isinstance(outcome_raw, dict) else {}
    outcome = SummaryOutcome(
        status=str(outcome_raw.get("status") or "No Booking"),
        reason=str(outcome_raw.get("reason") or "Not enough information to determine an outcome."),
    )

    return CallSummary(
        booking_snapshot=booking_snapshot,
        conversation_summary=conversation_summary,
        outcome=outcome,
        host_action=_as_str_list(parsed.get("host_action")),
        key_details=_as_str_list(parsed.get("key_details")),
        missing_information=_as_str_list(parsed.get("missing_information")),
    )


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
        max_tokens=1024,
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
    prompt = _SUMMARY_PROMPT + transcript

    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return await _call_anthropic(prompt)
    if settings.groq_api_key:
        return await _call_groq(prompt)
    if settings.openrouter_api_key:
        return await _call_openrouter(prompt)
    raise CallSummaryError("No LLM provider is configured (GROQ_API_KEY/ANTHROPIC_API_KEY/OPENROUTER_API_KEY)")


async def summarize_call(transcript: str | None, duration_seconds: float | None) -> CallSummary:
    """Pure function of the transcript + duration -- no DB access, callers
    persist the result themselves (see call_service.set_call_summary). Never
    raises: any failure (bad response, timeout, no provider configured)
    degrades to a safe fallback summary rather than propagating, since this
    must never crash on_pipeline_finished."""
    pre = _pre_check(transcript, duration_seconds)
    if pre is not None:
        return pre

    try:
        raw = await asyncio.wait_for(_call_llm_with_fallback(transcript or ""), timeout=20)
        return _parse_summary_response(raw)
    except Exception:
        logger.exception("Call summary generation failed")
        return _FAILED_SUMMARY
