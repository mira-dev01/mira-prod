"""Generic Response Compliance layer -- a quality GATE between the LLM and
downstream processors (tts), not a language-specific fix.

Sits last in the guard chain, immediately after response_shape_guard and
immediately before tts: it must see the actual final text about to be
spoken, after every rewriting guard (repetition_guard -> meta_commentary_guard
-> property_recommendation_guard -> escalation_guard -> premature_end_call_guard
-> response_shape_guard) has already run -- validating a draft any of those
could still change would mean checking the wrong text. Reuses the exact
buffering shape response_shape_guard.py already established (buffer
LLMTextFrames between LLMFullResponseStartFrame/LLMFullResponseEndFrame, then
run deterministic checks over the complete text) rather than inventing a new
mechanism.

Architectural contract (deliberately preserved, not incidental):
- The LLM still decides intent and content. Rules never construct a prompt,
  never call an LLM, never touch context.messages -- checking is pure and
  deterministic. The one exception, scoped narrowly and explicitly: the
  bounded regenerate-once path below (Response Validator), which fires ONLY
  on a hard FAIL and makes exactly one direct, non-streaming LLM call with a
  stronger style instruction -- never a second probabilistic check, never an
  unbounded retry loop, and completely separate from rule CHECKING itself
  (every Rule.check remains a pure function with no I/O).
- Never rewrites the response deterministically -- no regex substitution, no
  text patching. On FAIL, the only correction mechanism is the single
  bounded regeneration below; on WARNING, or if regeneration itself fails/
  times out/still fails validation, the original frame is pushed downstream
  completely unchanged. A structured log line and metrics are always
  emitted regardless of outcome.
- Extensible without modifying this file's own class: new rules (e.g. a
  PropertyNameRule, PricingConsistencyRule) are added by constructing
  ResponseComplianceProcessor with a longer `rules` list -- no plugin
  framework, no dependency injection, just a plain Python list, matching this
  codebase's existing precedent (validate_response_shape's own list-of-checks
  style in response_shape_guard.py).

Cost: one pass over one response's text (bounded by max_completion_tokens,
typically well under 1kB) plus a handful of dict/set lookups per rule -- no
allocation proportional to conversation history, no network call, no DB
access on the common PASS path (the overwhelming majority of turns). A FAIL
verdict triggers exactly one direct LLM call (see _regenerate_once), same
one-shot-completion pattern app/services/call_summary_service.py already
uses for its own non-streaming, non-pipecat LLM call -- capped at one retry
per turn, never a loop, with a hard timeout and safe fallback to the
original (uncorrected but never blocked) response on any failure.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from loguru import logger
from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transcriptions.language import Language

from app.config import settings
from app.voice.conversation_style import render_style_block
from app.voice.language_heuristics import MIN_CHARS_FOR_CONFIDENT_VERDICT, devanagari_ratio, has_hinglish_token

if TYPE_CHECKING:
    from pipecat.processors.aggregators.llm_context import LLMContext

    from app.voice.conversation_state import ConversationState

# One line, same "wouldn't sound out of place from a human receptionist"
# bar as slow_tool_filler.py's own filler texts -- spoken once,
# unconditionally, right before the bounded regenerate-once call so a
# validator FAIL never produces multi-second dead air on a live call.
_REGENERATION_FILLER_TEXT = "One moment."

REGENERATION_TIMEOUT_SECONDS = 8.0

ComplianceVerdict = Literal["PASS", "WARNING", "FAIL"]

# Mirrors state_prompt_sync._LANGUAGE_DISPLAY_NAMES exactly -- same two
# language families this codebase's language mechanism already recognizes.
# Not imported directly to avoid a state_prompt_sync -> response_compliance
# dependency for two dict entries; kept in sync deliberately, same way
# language_sync.py's own _HINDI_LANGUAGES set is independently declared
# rather than imported from state_prompt_sync.
_HINDI_LANGUAGES = {Language.HI, Language.HI_IN}
_ENGLISH_LANGUAGES = {Language.EN, Language.EN_IN, Language.EN_US}


@dataclass(frozen=True)
class RuleResult:
    """One rule's verdict on one response. retry_hook is only ever True on a
    FAIL -- a future retry mechanism can key off this field without this
    module changing; no retry is implemented here."""

    rule_name: str
    verdict: ComplianceVerdict
    reason: str
    confidence: float
    metadata: dict = field(default_factory=dict)

    @property
    def retry_hook(self) -> bool:
        return self.verdict == "FAIL"


class ComplianceRule(Protocol):
    """One deterministic, O(n)-over-the-response-text check. A rule never
    rewrites text and never raises for a normal mismatch -- it returns a
    verdict. Implementations must not perform network/DB/LLM calls."""

    name: str

    def check(self, text: str, state: "ConversationState | None") -> RuleResult: ...


def _expected_language_family(state: "ConversationState | None") -> str | None:
    """Reuses ConversationState exactly as state_prompt_sync._language_hint
    already does -- explicit guest preference wins over passive detection,
    same precedence, same two fields, no new state introduced. Returns
    "hindi", "english", or None (nothing known yet, e.g. before the first
    guest utterance -- not a violation, just nothing to check)."""
    if state is None:
        return None
    explicit = getattr(state, "explicit_language_preference", None)
    if explicit is not None:
        if explicit in _HINDI_LANGUAGES:
            return "hindi"
        if explicit in _ENGLISH_LANGUAGES:
            return "english"
    detected = getattr(state, "current_spoken_language", None)
    if detected is not None:
        if detected in _HINDI_LANGUAGES:
            return "hindi"
        if detected in _ENGLISH_LANGUAGES:
            return "english"
    return None


class LanguageComplianceRule:
    """First (and currently only) registered rule. Deterministic script/
    token heuristic only -- no NLP, no embeddings, no LLM call.

    Verdicts:
    - No expected language known yet (first turn before any guest speech, or
      an unrecognized Language enum value) -> PASS, nothing to check.
    - Response too short to classify confidently (e.g. "Sure.", a bare
      number read back) -> WARNING, low confidence, not a hard failure.
    - Expected English: PASS unless the response is mostly Devanagari
      (a script family switch that contradicts the expected language).
    - Expected Hindi/Hinglish: GOLDEN_RULES' own Urban Hinglish rule requires
      Roman-script rendering, so pure-Latin output is CORRECT, not a
      violation, by design -- only a response with a real Devanagari
      presence OR at least one common Hinglish token counts as compliant.
      Neither present -> FAIL: this is precisely the regression shape
      confirmed on the real call trace (STT/state/prompt hint all correctly
      say "Hindi/Hinglish expected", LLM output plain English).
    """

    name = "language_compliance"

    def check(self, text: str, state: "ConversationState | None") -> RuleResult:
        expected = _expected_language_family(state)
        if expected is None:
            return RuleResult(self.name, "PASS", "no expected language known yet", confidence=1.0)

        ratio, letter_count = devanagari_ratio(text)

        if letter_count < MIN_CHARS_FOR_CONFIDENT_VERDICT:
            return RuleResult(
                self.name,
                "WARNING",
                "response too short to classify confidently",
                confidence=0.3,
                metadata={"expected_language": expected, "observed_language": "indeterminate", "letter_count": letter_count},
            )

        has_hindi_signal = ratio > 0.05 or has_hinglish_token(text)
        observed = "hindi" if has_hindi_signal else "english"
        meta = {
            "expected_language": expected,
            "observed_language": observed,
            "devanagari_ratio": round(ratio, 3),
        }

        if expected == "english":
            if ratio > 0.3:
                return RuleResult(
                    self.name,
                    "FAIL",
                    "guest/state expected English but response is substantially Devanagari",
                    confidence=min(1.0, 0.5 + ratio),
                    metadata=meta,
                )
            return RuleResult(self.name, "PASS", "response matches expected English", confidence=1.0, metadata=meta)

        # expected == "hindi" (display name "Hinglish" elsewhere in this codebase)
        if has_hindi_signal:
            return RuleResult(
                self.name, "PASS", "response contains Hindi/Hinglish signal", confidence=1.0, metadata=meta
            )
        return RuleResult(
            self.name,
            "FAIL",
            "guest/state expected Hindi/Hinglish but response has no Devanagari and no "
            "recognized Hinglish token -- plain English reply to a Hindi/Hinglish-expected turn",
            confidence=0.85,
            metadata=meta,
        )


def _stronger_style_instruction(state: "ConversationState | None") -> str:
    """The "stronger Conversation Style instruction" the spec asks the one
    allowed regeneration to use -- reuses render_style_block (the exact same
    text StatePromptSyncProcessor already injects every turn) plus one
    additional, more forceful sentence. Deliberately does NOT invent a
    second, different style description -- a regeneration should be told
    the same style more emphatically, not a different one."""
    base = ""
    if state is not None and getattr(state, "conversation_style", None) is not None:
        base = render_style_block(state.conversation_style) + "\n"
    return (
        base
        + "Your previous reply did not follow the Conversation Style above -- rewrite your last reply now, "
        "keeping the exact same meaning and information, but strictly in the language/script specified. "
        "Output ONLY the corrected reply text, nothing else."
    )


async def _call_groq_once(messages: list[dict]) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        max_completion_tokens=400,
    )
    return response.choices[0].message.content or ""


async def _call_anthropic_once(messages: list[dict]) -> str:
    from anthropic import AsyncAnthropic

    system_messages = [m["content"] for m in messages if m.get("role") == "system"]
    other_messages = [m for m in messages if m.get("role") != "system"]
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=400,
        system="\n\n".join(system_messages),
        messages=other_messages,
    )
    return "".join(block.text for block in response.content if block.type == "text")


async def _call_openrouter_once(messages: list[dict]) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        messages=messages,
        max_completion_tokens=400,
    )
    return response.choices[0].message.content or ""


async def _regenerate_once(messages: list[dict], stronger_instruction: str) -> str | None:
    """Exactly ONE direct, non-streaming completion call -- same one-shot
    pattern as app/services/call_summary_service.py's _call_llm_with_fallback,
    reused here rather than reinvented, deliberately NOT built on
    pipeline.py's _build_llm()/pipecat streaming services (wired for the
    live function-calling voice pipeline, not a fit for a single one-shot
    correction call). Returns None (never raises) on any failure/timeout --
    callers must fall back to the original, uncorrected text rather than
    block the call or crash the pipeline. Uses the existing conversation
    messages plus one extra system instruction -- never a second, different
    prompt built from scratch."""
    regen_messages = [*messages, {"role": "system", "content": stronger_instruction}]

    async def _call() -> str:
        if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
            return await _call_anthropic_once(regen_messages)
        if settings.groq_api_key:
            return await _call_groq_once(regen_messages)
        if settings.openrouter_api_key:
            return await _call_openrouter_once(regen_messages)
        raise RuntimeError("No LLM provider configured for regeneration")

    try:
        text = await asyncio.wait_for(_call(), timeout=REGENERATION_TIMEOUT_SECONDS)
        return text.strip() or None
    except Exception:
        logger.exception("ResponseComplianceProcessor: regeneration call failed, keeping original response")
        return None


class ResponseComplianceProcessor(FrameProcessor):
    """Buffers each complete response (same shape as
    ResponseShapeValidatorProcessor) and runs every registered ComplianceRule
    over the final text once it's complete. On PASS/WARNING, pushes the
    original frames through unchanged. On FAIL with retry_hook set, attempts
    exactly ONE direct-LLM regeneration (see _regenerate_once) using the same
    conversation context plus a stronger Conversation Style instruction --
    if the regenerated text itself then validates clean, THAT text is pushed
    instead; on any regeneration failure/timeout, or if the correction still
    fails validation, the ORIGINAL text is pushed (this gate never blocks the
    call or leaves the guest with nothing). Never more than one regeneration
    per turn -- no loop, no repeated attempts. Sits after response_shape_guard,
    before tts."""

    def __init__(
        self,
        conversation_state: "ConversationState | None" = None,
        rules: list[ComplianceRule] | None = None,
        llm_context: "LLMContext | None" = None,
    ):
        super().__init__()
        self._state = conversation_state
        # No default rule list here -- which rules run is entirely the
        # caller's decision (pipeline.py), same as every other guard's
        # specific configuration is assembled at its own construction site,
        # not defaulted inside the processor class. Keeps this class generic
        # in fact, not just in name: it has zero opinion about which rules
        # exist. An empty list is valid (a no-op gate) rather than an error.
        self._rules: list[ComplianceRule] = rules if rules is not None else []
        # Optional -- only needed for the bounded regenerate-once path
        # (FAIL + retry_hook). Same object pipeline.py's `context` variable
        # already is, shared by reference with llm/state_prompt_sync/the
        # aggregators -- read-only here (messages are copied into a new
        # list for the one-shot call, this processor never mutates
        # context.messages itself, same "never touches context.messages"
        # discipline every other rule/check in this module already follows).
        self._llm_context = llm_context
        self._buffering = False
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._buffer = []
            # Deliberately NOT pushed yet -- unlike response_shape_guard.py's
            # own Start/End frames (which it always re-emits regardless of
            # whether it rewrote anything), this processor may substitute
            # the ENTIRE response including its Start/Text frames on a
            # successful regeneration, so nothing downstream (tts) can see
            # partial original text before a substitution decision is made.
            # Held here, pushed at LLMFullResponseEndFrame time alongside
            # whichever text (original or regenerated) is the final answer.
            return

        if self._buffering and isinstance(frame, LLMTextFrame):
            # Also withheld, same reasoning as above -- streaming the
            # original chunks downstream immediately would make a later
            # regeneration substitution impossible (tts would already have
            # started speaking the un-corrected original).
            self._buffer.append(frame.text)
            return

        if self._buffering and isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._buffer)
            self._buffering = False

            final_text = text
            if text.strip():
                final_text = await self._evaluate_and_maybe_regenerate(text)

            await self.push_frame(LLMFullResponseStartFrame())
            if final_text:
                await self.push_frame(LLMTextFrame(final_text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _evaluate_and_maybe_regenerate(self, text: str) -> str:
        should_regenerate = False
        for rule in self._rules:
            result = self._log_rule_result(rule, text)
            if result.retry_hook:
                should_regenerate = True

        if not should_regenerate or self._llm_context is None:
            return text

        return await self._attempt_regeneration(text)

    async def _attempt_regeneration(self, original_text: str) -> str:
        # Regeneration is a direct, non-streaming LLM call (up to
        # REGENERATION_TIMEOUT_SECONDS) sitting in the middle of the live
        # pipeline -- without a filler this is dead air on a phone call,
        # the exact confirmed-live failure mode slow_tool_filler.py already
        # exists to fix for slow tool calls. Same pattern reused here:
        # append_to_context=False so this never appears in the LLM's own
        # context on a later turn, pushed once, unconditionally, right
        # before the blocking call (no delayed-timer/cancel-on-fast-path
        # logic like slow_tool_filler's, since a regeneration is never
        # fast enough to skip it -- it's only ever triggered by a FAIL,
        # already the slow, rare path).
        await self.push_frame(TTSSpeakFrame(_REGENERATION_FILLER_TEXT, append_to_context=False))

        stronger_instruction = _stronger_style_instruction(self._state)
        regenerated = await _regenerate_once(list(self._llm_context.messages), stronger_instruction)

        if regenerated is None:
            logger.warning("ResponseComplianceProcessor: regeneration unavailable, keeping original response")
            return original_text

        # Re-validate the correction itself -- never trust a regeneration
        # blindly. If it still fails, fall back to the original rather than
        # risk a WORSE response reaching the guest; either way this is the
        # end of the line, no second regeneration attempt.
        for rule in self._rules:
            result = self._log_rule_result(rule, regenerated, log_prefix="post-regeneration")
            if result.verdict == "FAIL":
                logger.warning(
                    "ResponseComplianceProcessor: regeneration still failed validation, keeping original response"
                )
                return original_text

        logger.info("ResponseComplianceProcessor: regeneration passed validation, using corrected response")
        return regenerated

    def _log_rule_result(self, rule: ComplianceRule, text: str, log_prefix: str = "ResponseComplianceProcessor") -> RuleResult:
        rule_start = time.perf_counter()
        result = rule.check(text, self._state)
        processing_time_ms = (time.perf_counter() - rule_start) * 1000

        log_fields = {
            "rule": result.rule_name,
            "expected_language": result.metadata.get("expected_language"),
            "observed_language": result.metadata.get("observed_language"),
            "compliance_result": result.verdict,
            "confidence": result.confidence,
            "response_length": len(text),
            "processing_time_ms": round(processing_time_ms, 3),
            **result.metadata,
        }
        bound = logger.bind(**log_fields)

        if result.verdict == "FAIL":
            bound.warning("{}: FAIL -- {}", log_prefix, result.reason)
        elif result.verdict == "WARNING":
            bound.info("{}: WARNING -- {}", log_prefix, result.reason)
        else:
            # PASS is the overwhelming majority of turns and has zero
            # debugging value on its own -- trace, not debug, so
            # production's DEBUG-level sink (app/main.py) doesn't fill
            # with a line per compliant turn. FAIL/WARNING (the cases
            # anyone actually greps for) are unaffected.
            bound.trace("{}: PASS -- {}", log_prefix, result.reason)

        return result
