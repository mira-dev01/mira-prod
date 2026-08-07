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
- The LLM still decides intent and content. This module never constructs a
  prompt, never calls an LLM, never touches context.messages.
- Python verifies deterministic constraints only -- each Rule is a pure
  function of (text, ConversationState) to a PASS/WARNING/FAIL verdict, never
  a second probabilistic judgment call.
- Never rewrites the response. On FAIL/WARNING the frame is pushed downstream
  completely unchanged -- only a structured log line and an in-memory metrics
  counter are emitted. A future retry mechanism can hang off
  RuleResult.retry_hook without this processor's own logic changing.
- Extensible without modifying this file's own class: new rules (e.g. a
  PropertyNameRule, PricingConsistencyRule) are added by constructing
  ResponseComplianceProcessor with a longer `rules` list -- no plugin
  framework, no dependency injection, just a plain Python list, matching this
  codebase's existing precedent (validate_response_shape's own list-of-checks
  style in response_shape_guard.py).

Cost: one pass over one response's text (bounded by max_completion_tokens,
typically well under 1kB) plus a handful of dict/set lookups per rule -- no
allocation proportional to conversation history, no network call, no DB
access, no additional LLM call. Same frequency as every other guard already
in this pipeline (once per assistant turn).
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from loguru import logger
from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transcriptions.language import Language

if TYPE_CHECKING:
    from app.voice.conversation_state import ConversationState

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


# Common romanized Hindi/Hinglish function words -- the actual signal that
# distinguishes "genuine Hinglish reply, just happens to render in Latin
# script" (GOLDEN_RULES' own required style, system_prompt.py's Urban
# Hinglish rule) from "reply is plainly English despite Hindi being
# expected." Deliberately small and closed-class (pronouns/particles/
# common verbs a Hinglish sentence is very likely to contain at least one
# of), not an attempt at general language ID -- a lightweight heuristic per
# the spec, not NLP.
# Excludes any token that collides with a common English word ("the", "is",
# "to", "ka" as in "-ka" homonyms etc. were considered and rejected) --
# false-positiving on plain English text would defeat the rule's purpose.
_HINGLISH_TOKENS = frozenset(
    {
        "hai", "hain", "hoon", "hun", "aapka", "aapke", "aapki",
        "mein", "humein", "kaise", "kyun", "kyunki", "kahan", "kitna", "kitne",
        "nahi", "nahin", "haan", "bhi", "toh", "achha", "accha", "theek", "thik",
        "shukriya", "chahiye", "milega", "milegi", "karenge", "karna",
    }
)

_MIN_CHARS_FOR_CONFIDENT_VERDICT = 12


def _devanagari_ratio(text: str) -> tuple[float, int]:
    """O(n) single pass. Returns (devanagari_fraction_of_letters, letter_count)
    -- ratio is computed over alphabetic codepoints only (digits/punctuation/
    whitespace/property-name casing excluded), same discipline
    response_shape_guard.py's regex checks already use of only looking at
    signal-bearing characters."""
    devanagari = 0
    letters = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        if "DEVANAGARI" in unicodedata.name(ch, ""):
            devanagari += 1
    if letters == 0:
        return 0.0, 0
    return devanagari / letters, letters


def _has_hinglish_token(text: str) -> bool:
    # Strip trailing punctuation per word so "hai," / "kya?" still match --
    # cheap O(n) tokenization, no regex backtracking risk.
    for word in text.lower().split():
        stripped = word.strip(".,!?;:\"'()")
        if stripped in _HINGLISH_TOKENS:
            return True
    return False


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

        devanagari_ratio, letter_count = _devanagari_ratio(text)

        if letter_count < _MIN_CHARS_FOR_CONFIDENT_VERDICT:
            return RuleResult(
                self.name,
                "WARNING",
                "response too short to classify confidently",
                confidence=0.3,
                metadata={"expected_language": expected, "observed_language": "indeterminate", "letter_count": letter_count},
            )

        has_hindi_signal = devanagari_ratio > 0.05 or _has_hinglish_token(text)
        observed = "hindi" if has_hindi_signal else "english"
        meta = {
            "expected_language": expected,
            "observed_language": observed,
            "devanagari_ratio": round(devanagari_ratio, 3),
        }

        if expected == "english":
            if devanagari_ratio > 0.3:
                return RuleResult(
                    self.name,
                    "FAIL",
                    "guest/state expected English but response is substantially Devanagari",
                    confidence=min(1.0, 0.5 + devanagari_ratio),
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


class ResponseComplianceProcessor(FrameProcessor):
    """Buffers each complete response (same shape as
    ResponseShapeValidatorProcessor) and runs every registered ComplianceRule
    over the final text once it's complete. Never rewrites; always pushes the
    original frames through unchanged. Sits after response_shape_guard,
    before tts."""

    def __init__(
        self,
        conversation_state: "ConversationState | None" = None,
        rules: list[ComplianceRule] | None = None,
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
        self._buffering = False
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._buffer = []
            await self.push_frame(frame, direction)
            return

        if self._buffering and isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            await self.push_frame(frame, direction)
            return

        if self._buffering and isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._buffer)
            self._buffering = False

            if text.strip():
                self._evaluate(text)

            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _evaluate(self, text: str) -> None:
        for rule in self._rules:
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
                bound.warning("ResponseComplianceProcessor: FAIL -- {}", result.reason)
            elif result.verdict == "WARNING":
                bound.info("ResponseComplianceProcessor: WARNING -- {}", result.reason)
            else:
                # PASS is the overwhelming majority of turns and has zero
                # debugging value on its own -- trace, not debug, so
                # production's DEBUG-level sink (app/main.py) doesn't fill
                # with a line per compliant turn. FAIL/WARNING (the cases
                # anyone actually greps for) are unaffected.
                bound.trace("ResponseComplianceProcessor: PASS -- {}", result.reason)
