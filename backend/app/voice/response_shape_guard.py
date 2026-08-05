"""Final structural gate right before TTS -- validates the SHAPE of one
complete response (how many distinct questions/objectives it contains,
whether it reads as one continuous reply or several turns stitched
together), never its meaning.

Phase 4.3 (documentation/agent-conversation-improvement.md). Reframed
during review from a repetition problem into what it actually is: catalogue
item C3 (Phase 0.2) -- three separate questions concatenated into one wall
of text with no guest turn in between, confirmed live as recently as
2026-07-31 -- is NOT a repetition problem. RepetitionGuardProcessor's
word-overlap heuristic is specifically built to catch a sentence repeating
ITSELF; three genuinely DIFFERENT sentences glued together have low word
overlap by construction, so that guard structurally cannot see this shape.
This processor checks the response's STRUCTURE instead: how many distinct
questions/objectives/greetings/recommendation-blocks it contains, and
whether it ends on a complete thought -- never a second unreliable AI
judgment of "is this a good response," which would just add a second
unreliable judge in front of TTS instead of a real guarantee. Every check
below is a deterministic/mechanical pattern match, the same discipline the
other guards in this pipeline already use.

Sits LAST in the guard chain, immediately before tts -- after every other
guard's rewrites (repetition_guard -> meta_commentary_guard ->
property_recommendation_guard -> escalation_guard -> premature_end_call_guard,
docs/agents.md's pipeline-stage list) have already run, so it validates the
actual final text about to be spoken, not an intermediate draft any earlier
guard might still rewrite.

Always buffers the whole response (unlike escalation_phrase_guard's
conditional arming) -- this guard's whole point is judging the FINAL,
complete text, so there's no narrower "only after X tool fires" scope to
gate on. Latency cost: one response's worth of buffering (matches the
existing precedent set by escalation_phrase_guard.py's own buffering
window), not a new class of cost this pipeline hasn't already accepted.
"""

import re

from loguru import logger

from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.services.property.pitch_formatter import CONFIDENCE_INTROS
from app.voice.escalation_phrase_guard import SAFE_REPLACEMENT_TEXT

_GREETING_RE = re.compile(r"\b(hi|hello|namaste|hey)\b[!,. ]", re.IGNORECASE)

# A "connector" between two questions is a word that makes them read as one
# continuous thought a human would say in one breath ("...you like, and
# which dates...") rather than two independently-generated turns glued raw.
# Deliberately generous (covers English/Hindi/Hinglish connectors) so a
# single, legitimately connected two-part question is never flagged.
_QUESTION_CONNECTOR_RE = re.compile(
    r"\b(and|or|but|also|aur|ya|lekin|toh|so|since|because|kyunki)\b", re.IGNORECASE
)

# Catches both a tight run ("...", "??") and the real observed flood shape
# (catalogue item H2) of repeated, space-separated short punctuation tokens
# like ".. .. .. .." -- confirmed live, a degenerate-output failure that
# RepetitionGuardProcessor's own fragment-flood detection already targets
# from a different angle (this is belt-and-suspenders, not a replacement).
_DUPLICATED_PUNCTUATION_RE = re.compile(r"(\?\?+|\.\.\.+|--{2,}|(?:\.\.\s*){3,})")

# A response ending with no terminal punctuation, or ending on a bare
# conjunction/determiner, looks cut off mid-clause rather than genuinely
# finished. Deliberately does NOT include prepositions ("with", "for", "to")
# -- those are common, completely valid sentence-final words in natural
# spoken English questions ("who's this with?", "what's this for?",
# confirmed against catalogue item C3's own real text, which correctly ends
# "...go ahead with?" as a complete, valid question) -- only conjunctions
# and bare articles/determiners are reliable dangling-clause signals.
_TERMINAL_PUNCTUATION_RE = re.compile(r"[.!?]\s*$")
_DANGLING_TRAILING_WORDS = {
    "and", "but", "or", "the", "a", "an",
    "aur", "lekin", "ya", "toh", "ke", "ki", "ka",
}


def count_questions(text: str) -> int:
    return text.count("?")


def has_multiple_unconnected_questions(text: str) -> bool:
    """Catalogue item C3's exact shape: several question marks with nothing
    that reads as a single connected thought between them. Splits on each
    '?' and checks whether the text immediately following one question mark
    (before the next) starts with something that looks like an independent
    new sentence (capitalized start, no connector word bridging from the
    previous question) rather than a continuation. Only the FIRST word of
    the following segment is checked for a connector -- a connector word
    appearing later, inside that segment's own unrelated content (e.g.
    "...availability AND pricing..."), must not be mistaken for a bridge
    between the two questions themselves."""
    segments = [s.strip() for s in text.split("?") if s.strip()]
    if len(segments) < 3:
        # Two questions total (one '?' pair) is common and fine (GOLDEN_RULES
        # already permits a single follow-up); this check specifically
        # targets THREE OR MORE, the actual C3 shape, not routine two-part
        # exchanges.
        return False
    # segments[:-1] are the parts immediately preceding each '?' -- check the
    # transition point between consecutive question-bearing segments.
    unconnected_transitions = 0
    for i in range(len(segments) - 1):
        following = segments[i + 1]
        first_word = following.split()[0] if following.split() else ""
        if not _QUESTION_CONNECTOR_RE.fullmatch(first_word) and following[:1].isupper():
            unconnected_transitions += 1
    return unconnected_transitions >= 2


def count_greeting_openers(text: str) -> int:
    return len(_GREETING_RE.findall(text))


def has_duplicated_safe_line(text: str) -> bool:
    return text.count(SAFE_REPLACEMENT_TEXT) >= 2


def has_duplicated_punctuation(text: str) -> bool:
    return bool(_DUPLICATED_PUNCTUATION_RE.search(text))


def ends_mid_clause(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if not _TERMINAL_PUNCTUATION_RE.search(stripped):
        return True
    trailing_word = re.sub(r"[.!?]+$", "", stripped).split()[-1].lower() if stripped.split() else ""
    return trailing_word in _DANGLING_TRAILING_WORDS


def count_recommendation_blocks(text: str) -> int:
    return sum(text.count(intro) for intro in CONFIDENCE_INTROS.values())


def has_multiple_recommendation_blocks(text: str) -> bool:
    return count_recommendation_blocks(text) >= 2


def validate_response_shape(text: str) -> list[str]:
    """Returns a list of violation names (empty if the response is
    structurally clean) -- never rewrites text itself; the processor below
    decides what to do with a violation (currently: drop everything after
    the first clean, complete sentence, since that's always safe to keep)."""
    violations = []
    if has_multiple_unconnected_questions(text):
        violations.append("multiple_unconnected_questions")
    if count_greeting_openers(text) >= 2:
        violations.append("multiple_greetings")
    if has_duplicated_safe_line(text):
        violations.append("duplicated_safe_line")
    if has_duplicated_punctuation(text):
        violations.append("duplicated_punctuation")
    if has_multiple_recommendation_blocks(text):
        violations.append("multiple_recommendation_blocks")
    if ends_mid_clause(text):
        violations.append("ends_mid_clause")
    return violations


# \s* (not \s+) deliberately -- catalogue item C3's own real transcript text
# has NO space after the first '?' at all ("...interesting?Got it, Abhaya."),
# which is itself part of the concatenated-turns shape this guard exists to
# catch. Splitting only on "punctuation + required whitespace" would miss
# exactly this real, observed case.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s*")


def first_clean_sentence_or_original(text: str) -> str:
    """Deterministic resolution strategy for a shape violation: keep only
    the first complete sentence, since GOLDEN_RULES already establishes that
    one clean question/statement per turn is always the correct shape --
    never leave the guest with nothing (falls back to the original text if
    it can't be split into at least one real sentence, e.g. no terminal
    punctuation anywhere)."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    if not parts:
        return text
    return parts[0]


class ResponseShapeValidatorProcessor(FrameProcessor):
    """Buffers each complete response and validates its final shape before
    it reaches TTS -- structural/mechanical checks only, never a semantic
    judgment. Sits last in the guard chain (before tts)."""

    def __init__(self):
        super().__init__()
        self._buffering = False
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._buffer = []
            return

        if self._buffering and isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            return

        if self._buffering and isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._buffer)
            self._buffering = False

            if not text.strip():
                await self.push_frame(frame, direction)
                return

            violations = validate_response_shape(text)
            if violations:
                logger.warning(
                    "ResponseShapeValidatorProcessor: trimmed reply to its first clean sentence -- "
                    "violations: {}",
                    violations,
                )
            final_text = first_clean_sentence_or_original(text) if violations else text

            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(final_text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
