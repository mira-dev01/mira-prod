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

Streaming-first rewrite (response output architecture redesign): previously
buffered the ENTIRE response, unconditionally, on every turn, before
validating once at the end -- the same unconditional-buffer pattern that
broke native LLM streaming and was the #1 architectural problem this
redesign fixes. Now streams every LLMTextFrame through IMMEDIATELY as it
arrives, exactly like RepetitionGuardProcessor already does for its own,
structurally similar problem (repetition_guard.py's own docstring: "stream
every LLMTextFrame straight through immediately by default -- no buffering,
no added latency to the normal case"). Only the CURRENT, still-incomplete
sentence fragment is ever held (bounded to one sentence, never the whole
response) -- the same buffering unit repetition_guard already uses.

Five of the six checks (multiple_unconnected_questions, multiple_greetings,
duplicated_safe_line, duplicated_punctuation, multiple_recommendation_blocks)
are monotonic: once true over the accumulated text, they never flip back to
false as more text streams in, so re-running validate_response_shape after
each newly-completed sentence and cutting the instant any of them fires is
mathematically equivalent to today's whole-buffer version -- both keep only
the sentence(s) that existed before the first true violation, which in
every real/tested case is exactly the first sentence (confirmed directly:
every violation-triggering example in this module's own test suite first
trips at sentence index >= 1, never at index 0, so streaming sentence 0
immediately and cutting everything after the violation is detected produces
byte-identical output to the old first_clean_sentence_or_original(text)
behavior). The sixth check, ends_mid_clause, is NOT monotonic in this sense
-- it's only meaningful against the FINAL, complete text (every non-final
sentence looks "cut off" simply because more is still coming) -- so it is
still evaluated exactly once, at LLMFullResponseEndFrame, same as before.

Latency cost, precisely: sentence 0 reaches tts as fast as the LLM itself
streams it -- zero added buffering delay for the overwhelming majority of
turns (no violation ever fires). Only on an actual violation does the
guest hear one sentence before the guard reacts, the exact same accepted,
explicitly-bounded tradeoff repetition_guard.py's own docstring already
documents for its class of problem.
"""

import re

from loguru import logger

from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.services.property.pitch_formatter import CONFIDENCE_INTROS
from app.voice.conversation_quality import ConversationQuality, ValidationResult
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


# The five checks that are monotonic over accumulating text (see module
# docstring) -- evaluated incrementally, after each completed sentence,
# by the streaming processor below. ends_mid_clause is deliberately
# excluded here; it is only meaningful against the final, complete
# response and is checked once, separately, at LLMFullResponseEndFrame.
def _incremental_violations(text: str) -> list[str]:
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
    return violations


def validate_response_shape(text: str) -> list[str]:
    """Returns a list of violation names (empty if the response is
    structurally clean) -- the full six-check validation against a COMPLETE
    response, unchanged from before this redesign. Used by tests and by
    ResponseShapeStreamProcessor's own final ends_mid_clause check; the
    processor's live streaming path uses _incremental_violations (five of
    these six checks) turn-by-turn instead, for the reasons in the module
    docstring."""
    violations = _incremental_violations(text)
    if ends_mid_clause(text):
        violations.append("ends_mid_clause")
    return violations


# \s* (not \s+) deliberately -- catalogue item C3's own real transcript text
# has NO space after the first '?' at all ("...interesting?Got it, Abhaya."),
# which is itself part of the concatenated-turns shape this guard exists to
# catch. Splitting only on "punctuation + required whitespace" would miss
# exactly this real, observed case. Same regex repetition_guard.py's own
# _SENTENCE_SPLIT_RE uses conceptually (sentence-boundary lookbehind),
# reused here rather than reinvented -- this module's own boundary needs
# the looser \s* variant for the no-space case above, so it is declared
# separately rather than importing repetition_guard's stricter \s+ version.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s*")

# Capturing variant of the same boundary, used only by the streaming
# processor below -- re.split with a capturing group keeps the matched
# whitespace itself as its own list element instead of discarding it, so
# re-forwarding sentences as SEPARATE frames never silently drops the space
# between them (found live in this rewrite: the non-capturing split above is
# correct for first_clean_sentence_or_original, which only ever picks ONE
# resulting piece and never rejoins pieces, but forwarding multiple pieces
# as separate frames needs every character preserved, including the
# boundary whitespace itself).
_SENTENCE_SPLIT_CAPTURING_RE = re.compile(r"((?<=[.!?])\s*)")


def first_clean_sentence_or_original(text: str) -> str:
    """Deterministic resolution strategy for a shape violation: keep only
    the first complete sentence, since GOLDEN_RULES already establishes that
    one clean question/statement per turn is always the correct shape --
    never leave the guest with nothing (falls back to the original text if
    it can't be split into at least one real sentence, e.g. no terminal
    punctuation anywhere). Still used by tests for the pure-function
    contract; the live streaming processor achieves the same effect by
    construction (stream sentence 0 immediately, stop forwarding once a
    violation fires), not by calling this function on a fully buffered
    response."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    if not parts:
        return text
    return parts[0]


class ResponseShapeValidatorProcessor(FrameProcessor):
    """Streams every LLMTextFrame through immediately as it arrives, holding
    back only the current incomplete sentence fragment (bounded to one
    sentence, never the whole response). Re-validates the five monotonic
    shape checks after each newly-completed sentence and, the instant any
    fires, stops forwarding further sentences for the rest of this response
    -- since the violating sentence is by construction never the first one
    in every real case this guard exists to catch (see module docstring),
    this reproduces "keep only the first clean sentence" as an emergent
    effect of streaming, not by buffering and trimming after the fact.
    ends_mid_clause is checked once, at LLMFullResponseEndFrame, against the
    complete forwarded text -- see module docstring for why it can't be
    checked incrementally. Sits last in the guard chain (before tts)."""

    def __init__(self, quality: ConversationQuality | None = None):
        super().__init__()
        self._quality = quality
        self._sentence_buffer = ""
        self._forwarded_text = ""
        # Whitespace that trailed the LAST successfully-forwarded sentence,
        # held back until the NEXT sentence also passes its own check (see
        # _consume's own comment) -- never forwarded on its own, never
        # forwarded ahead of proof that real content follows it.
        self._pending_separator = ""
        self._cutting = False
        self._turn_index = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._sentence_buffer = ""
            self._forwarded_text = ""
            self._pending_separator = ""
            self._cutting = False
            self._turn_index += 1
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            if self._cutting:
                # Silently dropped -- the guest just doesn't hear the rest
                # of this response, rather than hearing it concatenated
                # with unrelated later turns. Same accepted tradeoff
                # repetition_guard.py already documents for its own cut.
                return
            await self._consume(frame.text, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if not self._cutting and self._sentence_buffer.strip():
                # A final, incomplete-looking fragment with no more text
                # coming -- flush it (with any still-pending separator
                # ahead of it, now proven real since this fragment IS the
                # rest of the response), then run the one check that's
                # only meaningful now that the response is truly complete.
                final_fragment = self._pending_separator + self._sentence_buffer
                if ends_mid_clause(self._forwarded_text + final_fragment):
                    logger.warning(
                        "ResponseShapeValidatorProcessor: response ends mid-clause -- "
                        "flushing the trailing fragment as-is (never silently dropped)"
                    )
                await self.push_frame(LLMTextFrame(final_fragment))
                self._forwarded_text += final_fragment
                self._sentence_buffer = ""
                self._pending_separator = ""
            elif not self._cutting and self._forwarded_text and ends_mid_clause(self._forwarded_text):
                logger.warning(
                    "ResponseShapeValidatorProcessor: response ends mid-clause"
                )
            self._cutting = False
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _consume(self, text: str, direction: FrameDirection) -> None:
        self._sentence_buffer += text
        # Capturing split (see _SENTENCE_SPLIT_CAPTURING_RE's own comment)
        # so no character -- including inter-sentence whitespace -- is ever
        # dropped when re-forwarding sentences as separate frames. raw_parts
        # alternates [sentence, boundary_ws, sentence, boundary_ws, ...,
        # trailing_incomplete_or_empty]. Each sentence's OWN trailing
        # boundary whitespace is deliberately NOT merged into it here
        # (unlike an earlier version of this method) -- that whitespace
        # only makes sense as a separator BEFORE the next sentence, and if
        # a cut happens right after this one, there is no next sentence to
        # separate from; leaving it attached would forward a dangling
        # trailing space with nothing after it. Instead each sentence
        # picked up its OWN leading separator from the previous iteration.
        raw_parts = _SENTENCE_SPLIT_CAPTURING_RE.split(self._sentence_buffer)
        sentences = raw_parts[0:-1:2]
        separators = raw_parts[1::2]
        # The last element is whatever's left after the final completed
        # boundary -- may be an incomplete sentence (more text still
        # streaming in) or empty. Keep it in the buffer, only judge/forward
        # the completed ones ahead of it.
        self._sentence_buffer = raw_parts[-1]

        for sentence, trailing_sep in zip(sentences, separators):
            candidate_text = self._forwarded_text + sentence
            violations = _incremental_violations(candidate_text)
            if violations:
                logger.warning(
                    "ResponseShapeValidatorProcessor: cutting the rest of this response -- "
                    "violations: {}",
                    violations,
                )
                if self._quality is not None:
                    self._quality.record(
                        ValidationResult(
                            rule="shape_compliance",
                            severity="WARNING",
                            confidence=1.0,
                            metadata={"violations": violations},
                            turn_index=self._turn_index,
                        )
                    )
                self._cutting = True
                return
            # The separator BEFORE this sentence (carried over from the
            # previous iteration, held back until now) is only forwarded
            # once THIS sentence has itself passed its own check -- proof
            # there really is more real content after it. This is what
            # prevents a dangling trailing space from going out ahead of a
            # sentence that then gets cut (found live in this rewrite: the
            # naive "forward sentence + its own trailing separator
            # immediately" version sent the separator BEFORE knowing
            # whether the next sentence would violate).
            await self.push_frame(LLMTextFrame(self._pending_separator + sentence), direction)
            self._forwarded_text = candidate_text
            self._pending_separator = trailing_sep
