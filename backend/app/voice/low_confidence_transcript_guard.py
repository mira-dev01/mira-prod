"""Decides what to do with a transcript Sarvam was unsure about, without ever
putting words in the guest's mouth.

`language_probability` is Sarvam's confidence in which LANGUAGE it heard, not
how accurate the transcript is. A short place name ("Koramangala", "Indira
Nagar") belongs clearly to neither Hindi nor English, so it scores low even
when it is transcribed perfectly. The earlier version of this guard replaced
every transcript under the threshold with an English sentence recorded as the
guest's turn; on a live call it replaced four correct transcripts, the model
believed the guest kept saying "sorry, say that again", and asked the same
question four times.

Now one pure function, `classify`, is the single copy of the rule (the audio
telemetry observer calls it too, so the two cannot drift):

  pass          -- use the transcript as it is.
  pass_flagged  -- keep the real text, but tell the model (state_prompt_sync) to
                   confirm any place, number or date back to the guest.
  inaudible     -- the text is not Hindi or English at all; it becomes the marker
                   `[inaudible]`, which the system prompt tells the model to answer by
                   asking the guest to repeat. Never a sentence in the guest's mouth.

Deterministic only, no LLM call (CLAUDE.md: validators must not regenerate).
"""

import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.services.geo.text import normalize, skeleton, strip_filler
from app.voice.conversation_state import ConversationState

# Below this, a transcript that none of the exceptions in `classify` cover is flagged.
# Still not calibrated against real calls; every decision is logged at info (without
# guest text) so it can be.
DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD = 0.4

# What replaces the text of a transcript in a script Mira does not speak. Plain
# ASCII with no letters of any language, so it cannot count as a Hindi or English turn.
INAUDIBLE_MARKER = "[inaudible]"

Decision = Literal["pass", "pass_flagged", "inaudible"]

# An utterance this short has too little text for a language-ID score to mean anything.
_SHORT_UTTERANCE_MAX_TOKENS = 3
_SUPPORTED_SCRIPTS = {"LATIN", "DEVANAGARI"}
_MAX_ENTITY_WINDOW = 6
# Speech-to-text may split one name into more words than the stored spelling has
# ("Indira Nagar" for "Indiranagar"), so windows run this much wider than the names.
_WINDOW_SLACK_TOKENS = 2
_MIN_SKELETON_LEN = 5

_YES_NO_WORDS = {
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "sure", "right", "correct", "fine",
    "haan", "han", "ha", "ji", "nahi", "nahin", "na", "theek", "thik", "accha", "achha",
    "हाँ", "हां", "हा", "जी", "नहीं", "नही", "ना", "ठीक", "अच्छा",
}
_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "lakh", "lac", "double", "triple",
    "ek", "do", "teen", "char", "chaar", "paanch", "panch", "chhe", "chhah", "che", "saat",
    "aath", "nau", "das", "gyarah", "barah", "terah", "chaudah", "pandrah", "solah", "satrah",
    "atharah", "unnees", "bees", "tees", "chalis", "pachas", "saath", "sattar", "assi", "nabbe",
    "sau", "hazaar", "hazar",
    "एक", "दो", "तीन", "चार", "पाँच", "पांच", "छह", "छः", "सात", "आठ", "नौ", "दस", "ग्यारह",
    "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस", "बीस", "तीस", "चालीस",
    "पचास", "साठ", "सत्तर", "अस्सी", "नब्बे", "सौ", "हज़ार", "हजार", "लाख", "डबल", "ट्रिपल",
}
_PUNCT = ".,!?;:।\"'()"


@dataclass(frozen=True)
class KnownEntities:
    """Names the host's guests really say (cities, property names, localities). A
    transcript that contains one is trusted whatever its language score is."""

    compact: frozenset[str] = frozenset()
    skeletons: frozenset[str] = frozenset()
    max_tokens: int = 1

    @classmethod
    def from_names(cls, names) -> "KnownEntities":
        compact: set[str] = set()
        skeletons: set[str] = set()
        max_tokens = 1
        for name in names:
            normalized = strip_filler(normalize(name))
            key = normalized.replace(" ", "")
            if not key:
                continue  # e.g. a Devanagari-only name: normalize() folds it to nothing
            compact.add(key)
            max_tokens = max(max_tokens, min(len(normalized.split()), _MAX_ENTITY_WINDOW))
            sk = skeleton(normalized)
            if len(sk) >= _MIN_SKELETON_LEN:
                skeletons.add(sk)
        return cls(frozenset(compact), frozenset(skeletons), max_tokens)

    def matches(self, text: str) -> bool:
        if not self.compact:
            return False
        tokens = strip_filler(normalize(text)).split()
        for size in range(1, min(self.max_tokens + _WINDOW_SLACK_TOKENS, len(tokens)) + 1):
            for start in range(len(tokens) - size + 1):
                window = tokens[start : start + size]
                if "".join(window) in self.compact:
                    return True
                sk = skeleton(" ".join(window))
                if len(sk) >= _MIN_SKELETON_LEN and sk in self.skeletons:
                    return True
        return False


@dataclass(frozen=True)
class Classification:
    decision: Decision
    reason: str
    token_count: int
    script: str
    language_code: str | None
    probability: float | None


def _dominant_script(text: str) -> str:
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            script = unicodedata.name(ch, "UNKNOWN").split(" ", 1)[0]
            counts[script] = counts.get(script, 0) + 1
    return max(counts, key=counts.get) if counts else "NONE"


def _is_numeric_or_yes_no(tokens: list[str]) -> bool:
    """Every token is a digit string, a number word or a yes/no word: a phone number,
    a guest count, "haan ji". Wrong ones are costly, and they score low for the same
    reason a place name does."""
    if not tokens:
        return False
    for raw in tokens:
        token = raw.strip(_PUNCT).lower()
        if not token:
            continue
        if any(ch.isdigit() for ch in token) or token in _NUMBER_WORDS or token in _YES_NO_WORDS:
            continue
        return False
    return True


def classify_transcript(
    text: str,
    probability: float | None,
    language_code: str | None,
    known_entities: KnownEntities | None = None,
    threshold: float = DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD,
) -> Classification:
    """The rule, with the reason it fired (for the log). `language_code` is carried
    for the log line only: the decision never keys on it, because Sarvam labelled
    the correctly transcribed English "Koramangala" as pa-IN."""
    tokens = text.split()
    script = _dominant_script(text)

    def result(decision: Decision, reason: str) -> Classification:
        return Classification(decision, reason, len(tokens), script, language_code, probability)

    if known_entities is not None and known_entities.matches(text):
        return result("pass", "known_entity")
    if len(tokens) <= _SHORT_UTTERANCE_MAX_TOKENS:
        return result("pass", "short_utterance")
    if _is_numeric_or_yes_no(tokens):
        return result("pass", "numeric_or_yes_no")
    if script != "NONE" and script not in _SUPPORTED_SCRIPTS:
        return result("inaudible", "unsupported_script")
    if probability is not None and probability < threshold:
        return result("pass_flagged", "low_language_probability")
    return result("pass", "confident" if probability is not None else "no_score")


def classify(
    text: str,
    probability: float | None,
    language_code: str | None,
    known_entities: KnownEntities | None = None,
    threshold: float = DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD,
) -> Decision:
    return classify_transcript(text, probability, language_code, known_entities, threshold).decision


class LowConfidenceTranscriptGuardProcessor(FrameProcessor):
    """Applies `classify` to every transcript, right after stt and before
    silence_watchdog/language_sync/the user aggregator."""

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD,
        known_entities: KnownEntities | None = None,
        conversation_state: ConversationState | None = None,
    ):
        super().__init__()
        self._threshold = threshold
        self._known_entities = known_entities
        self._conversation_state = conversation_state
        # How many transcripts took each decision this call (pass / pass_flagged / inaudible), kept for the
        # per-call scorecard (app/services/call_scorecard.py). Counts only; never the guest's words.
        self.decision_counts: Counter[str] = Counter()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            probability, language_code = self._extract_signals(frame)
            c = classify_transcript(frame.text.strip(), probability, language_code, self._known_entities, self._threshold)
            self.decision_counts[c.decision] += 1
            # Never the guest's words: the score and shape are what the threshold is tuned from.
            logger.info(
                "low_confidence_guard_decision decision={} reason={} language={} probability={} tokens={} script={}",
                c.decision,
                c.reason,
                c.language_code,
                c.probability,
                c.token_count,
                c.script,
            )
            if self._conversation_state is not None:
                # Always reflects the latest guest transcript, so it is a one-turn hint by construction.
                self._conversation_state.last_transcript_low_confidence = c.decision == "pass_flagged"
            if c.decision == "inaudible":
                frame.text = INAUDIBLE_MARKER

        await self.push_frame(frame, direction)

    @staticmethod
    def _extract_signals(frame: TranscriptionFrame) -> tuple[float | None, str | None]:
        """language_probability / language_code from the raw Sarvam response pipecat
        attaches to frame.result. (None, None) when absent (a fixed language_code,
        another STT provider): the guard then falls back to the text-only rules."""
        result = frame.result
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            return None, None
        probability = data.get("language_probability")
        language_code = data.get("language_code")
        return (
            float(probability) if isinstance(probability, (int, float)) else None,
            language_code if isinstance(language_code, str) else None,
        )
