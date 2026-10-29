"""Knowledge-base grounding guard: nothing about a property may be SPOKEN
unless it is supported by what is actually on file.

Background (confirmed live): a guest was told a property is "6 hours from
Delhi" when no such thing was in the property's information. Prompt rules
alone can't stop that -- the model answers from its own world knowledge. This
guard is the code-level backstop, complementing search_faq's retrieval
(app/services/property/kb_retrieval.py), which hands the model only matching
facts:

  * everything the agent was told this call -- the facts search_faq returned,
    other tools' results (prices, dates, recommendations), and the property
    details given in the prompt (name, city, check-in/out times, ...) -- forms
    the SUPPORT CORPUS;
  * each spoken sentence is checked, deterministically (regex/lexicon, no LLM,
    no regeneration -- CLAUDE.md's "validators must not introduce hidden LLM
    regeneration" invariant), for claims the corpus does not support:
      - measures: distances and travel/stay durations ("6 hours", "10
        minutes", "2 km") and clock times ("10 pm", "14:00");
      - places: capitalized names in a sentence about location/distance;
      - topics: policy/location/amenity subjects ("pets", "airport",
        "beach", "pool", "refund", ...) asserted with no mention on file.
  * a sentence that fails is replaced with the fixed not-on-file line
    (enforce mode) or only logged (shadow mode), and a gap is recorded for
    the host.

Support-based, not call-based: a claim is fine if the corpus supports it
regardless of which turn the fact was retrieved in, and blocked if nothing on
file supports it even when search_faq was called. That covers "answered
without searching" (nothing was retrieved to support it) and "searched,
found nothing, answered anyway" with one rule.

Deliberately narrow: it catches the specific, checkable claim classes above.
It cannot catch a fluent invented opinion with no measure/place/topic in it
("it's a lovely quiet area") -- the prompt rules and search_faq's fact-only
results are what constrain that.

Position in the pipeline: after the LLM, streaming sentence by sentence (a
sentence is held only until its terminator arrives, which TTS needs anyway),
so replies that make no checkable claim add no latency.

Modes (settings.kb_guard_mode): "shadow" logs `kb_guard_would_block` and
changes nothing; "enforce" rewrites; "off" doesn't install the guard.
Fails open on its own errors -- a guard bug must never silence a live call.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.services.amenity_taxonomy import _AMENITY_SYNONYMS, canonicalize_amenity

# --- number / measure extraction -------------------------------------------

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "ninety": 90, "hundred": 100,
}  # fmt: skip
_NUM = r"(?:\d+(?:\.\d+)?|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"

_HOUR_UNITS = r"hours?|hrs?|ghant\w*|घंट\w*"
_MINUTE_UNITS = r"minutes?|mins?|मिनट"
_KM_UNITS = r"kilomet(?:er|re)s?|kms?|किलोमीटर|किमी"
_METRE_UNITS = r"metres?|meters?"
_MILE_UNITS = r"miles?"
_UNIT = rf"(?:{_HOUR_UNITS}|{_MINUTE_UNITS}|{_KM_UNITS}|{_METRE_UNITS}|{_MILE_UNITS})"

_MEASURE_RE = re.compile(rf"(?<![\w.])(?P<num>{_NUM})\s*-?\s*(?P<unit>{_UNIT})\b", re.IGNORECASE)
_RANGE_RE = re.compile(
    rf"(?<![\w.])(?P<a>{_NUM})\s*(?:-|to|–)\s*(?P<b>{_NUM})\s*-?\s*(?P<unit>{_UNIT})\b", re.IGNORECASE
)
_AND_A_HALF_RE = re.compile(rf"(?P<num>{_NUM})\s+and\s+a\s+half\s+(?P<unit>{_HOUR_UNITS}|{_KM_UNITS})\b", re.IGNORECASE)
_AN_HOUR_RE = re.compile(r"\b(?:an|one)\s+hour\b", re.IGNORECASE)
_HALF_HOUR_RE = re.compile(r"\bhalf\s+(?:an\s+)?hour\b", re.IGNORECASE)
_AN_HOUR_AND_HALF_RE = re.compile(r"\ban\s+hour\s+and\s+a\s+half\b", re.IGNORECASE)
_VAGUE_RE = re.compile(
    rf"\b(?:a\s+few|couple\s+of|several)\s+(?P<unit>{_HOUR_UNITS}|{_MINUTE_UNITS}|{_KM_UNITS})\b", re.IGNORECASE
)

_CLOCK_AMPM_RE = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?m\b\.?", re.IGNORECASE)
_CLOCK_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _to_number(token: str) -> float:
    token = token.lower()
    return float(_NUMBER_WORDS[token]) if token in _NUMBER_WORDS else float(token)


def _canonical_measure(value: float, unit: str) -> tuple[str, float] | None:
    unit = unit.lower()
    if re.fullmatch(_HOUR_UNITS, unit, re.IGNORECASE):
        return ("time", value * 60)
    if re.fullmatch(_MINUTE_UNITS, unit, re.IGNORECASE):
        return ("time", value)
    if re.fullmatch(_KM_UNITS, unit, re.IGNORECASE):
        return ("dist", value * 1000)
    if re.fullmatch(_METRE_UNITS, unit, re.IGNORECASE):
        return ("dist", value)
    if re.fullmatch(_MILE_UNITS, unit, re.IGNORECASE):
        return ("dist", value * 1609)
    return None


def extract_measures(text: str) -> set[tuple[str, float]]:
    """Distances (metres) and durations (minutes) stated in `text`, digits or
    words, any of hours/minutes/km/metres/miles ("6 hours" == "six hours" ==
    "360 minutes"). "an hour" -> 60 minutes, "half an hour" -> 30."""
    measures: set[tuple[str, float]] = set()
    text = text or ""
    for match in _RANGE_RE.finditer(text):
        for group in ("a", "b"):
            measure = _canonical_measure(_to_number(match.group(group)), match.group("unit"))
            if measure:
                measures.add(measure)
    for match in _MEASURE_RE.finditer(text):
        measure = _canonical_measure(_to_number(match.group("num")), match.group("unit"))
        if measure:
            measures.add(measure)
    for match in _AND_A_HALF_RE.finditer(text):
        measure = _canonical_measure(_to_number(match.group("num")) + 0.5, match.group("unit"))
        if measure:
            measures.add(measure)
    if _AN_HOUR_RE.search(text):
        measures.add(("time", 60.0))
    if _HALF_HOUR_RE.search(text):
        measures.add(("time", 30.0))
    if _AN_HOUR_AND_HALF_RE.search(text):
        measures.add(("time", 90.0))
    return measures


def _vague_kinds(text: str) -> set[str]:
    kinds: set[str] = set()
    for match in _VAGUE_RE.finditer(text or ""):
        measure = _canonical_measure(1, match.group("unit"))
        if measure:
            kinds.add(measure[0])
    return kinds


def extract_clock_times(text: str) -> set[int]:
    """Clock times as minutes after midnight ("2 PM" == "14:00" == 840)."""
    times: set[int] = set()
    for match in _CLOCK_AMPM_RE.finditer(text or ""):
        hour = int(match.group(1)) % 12
        if match.group(3).lower() == "p":
            hour += 12
        times.add(hour * 60 + int(match.group(2) or 0))
    for match in _CLOCK_24H_RE.finditer(text or ""):
        times.add(int(match.group(1)) * 60 + int(match.group(2)))
    return times


def _measure_supported(measure: tuple[str, float], corpus: set[tuple[str, float]]) -> bool:
    kind, value = measure
    return any(k == kind and abs(v - value) <= max(0.5, value * 0.01) for k, v in corpus)


# --- topics -----------------------------------------------------------------

# Subjects a guest asks about and a model likes to answer from habit. A
# sentence asserting one of these needs the same subject to appear somewhere
# on file. Surface forms only -- no inference, and amenity subjects come from
# the same synonym map recommend_properties already uses.
_TOPIC_FORMS: dict[str, str] = {
    "pets": r"pets?|dogs?|cats?|pet[- ]friendly",
    "smoking": r"smok\w+|cigarettes?",
    "alcohol": r"alcohol|liquor|booze|drinking",
    "party": r"part(?:y|ies)|loud music|events?",
    "visitors": r"visitors?|outsiders?",
    "cancellation": r"refunds?|cancel\w*",
    "deposit": r"deposit",
    "airport": r"airport",
    "railway": r"railway|train station|bus stand|bus station|bus stop|metro",
    "beach": r"beach(?:es)?",
    "market": r"markets?|malls?|supermarkets?|grocery|groceries",
    "restaurants": r"restaurants?|caf[eé]s?|dhabas?",
    "medical": r"hospitals?|clinics?|pharmac\w+|chemists?|doctors?",
    "atm": r"atms?|banks?",
    "transport": r"taxis?|cabs?|uber|ola|auto[- ]?rickshaws?",
    "rental": r"scooters?|bikes?|motorbikes?",
    "nature": r"rivers?|lakes?|waterfalls?|trek\w*|hills?|temples?|monastery|fort",
    "power": r"generators?|inverters?|power backup|power cuts?",
    "hot_water": r"hot water|geysers?|heaters?",
}
_TOPIC_RES = {topic: re.compile(rf"\b(?:{forms})\b", re.IGNORECASE) for topic, forms in _TOPIC_FORMS.items()}
_AMENITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_AMENITY_SYNONYMS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


def extract_topics(text: str) -> set[str]:
    topics = {topic for topic, regex in _TOPIC_RES.items() if regex.search(text or "")}
    topics |= {f"amenity:{canonicalize_amenity(m)}" for m in _AMENITY_RE.findall(text or "")}
    return topics


# --- sentence analysis ------------------------------------------------------

# A sentence that says it DOESN'T know / will check is not asserting anything.
_DISCLAIMER_RE = re.compile(
    r"don'?t have|do not have|not on file|no information|not sure|can'?t say|cannot say|can'?t confirm|rather not guess|"
    r"let me check|i'?ll check|i will check|noted it|let the host know|host will",
    re.IGNORECASE,
)
_RELATION_RE = re.compile(
    r"\b(near|nearby|close to|next to|away|from|distance|walk\w*|drive|driving|minutes?|hours?|km|located|opposite|"
    r"beside|around|reach)\b",
    re.IGNORECASE,
)
_CAPITALIZED_MID_RE = re.compile(r"(?<![.!?।]\s)(?<!^)\b[A-Z][a-zA-Z]{2,}\b")
_ALWAYS_ALLOWED_CAPITALIZED = frozenset(
    {
        "mira", "namaste", "hindi", "english", "hinglish", "whatsapp", "airbnb", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december", "india", "sure", "okay", "great", "perfect",
        "wonderful", "lovely", "absolutely", "thanks", "thank", "sorry", "please", "hello", "yes", "got", "right",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Violation:
    kind: str  # measure | clock_time | vague_measure | place | topic
    detail: str


class SupportCorpus:
    """Everything the agent has legitimately been told this call. Grows as
    tool results arrive; derived sets are cached until the next append."""

    def __init__(self) -> None:
        self._facts: list[str] = []  # search_faq facts: ground measures, times, topics, places
        self._tools: list[str] = []  # other tool results: same
        self._static: list[str] = []  # property details given in the prompt: same
        self._guest: list[str] = []  # the guest's own words: ground times and place names only
        self._dirty = True
        self._measures: set[tuple[str, float]] = set()
        self._vague: set[str] = set()
        self._times: set[int] = set()
        self._topics: set[str] = set()
        self._words: set[str] = set()

    def add_facts(self, texts: list[str]) -> None:
        self._facts.extend(texts)
        self._dirty = True

    def add_tool_text(self, text: str) -> None:
        self._tools.append(text)
        self._dirty = True

    def set_static(self, text: str) -> None:
        self._static = [text] if text else []
        self._dirty = True

    def set_guest_text(self, text: str) -> None:
        self._guest = [text] if text else []
        self._dirty = True

    def _rebuild(self) -> None:
        supporting = " \n".join(self._facts + self._tools + self._static)
        everything = supporting + " \n" + " \n".join(self._guest)
        self._measures = extract_measures(everything)
        # A vague quantity ("a few hours") is supported only if the same vague
        # phrasing is itself on file -- a specific "10 minutes" elsewhere does not
        # make "a few hours" true.
        self._vague = _vague_kinds(supporting)
        self._times = extract_clock_times(everything)
        self._topics = extract_topics(supporting)  # the guest asking about a topic doesn't make it true
        self._words = set(re.findall(r"[a-z]{3,}", everything.lower()))
        self._dirty = False

    @property
    def measures(self) -> set[tuple[str, float]]:
        if self._dirty:
            self._rebuild()
        return self._measures

    @property
    def vague_kinds(self) -> set[str]:
        if self._dirty:
            self._rebuild()
        return self._vague

    @property
    def times(self) -> set[int]:
        if self._dirty:
            self._rebuild()
        return self._times

    @property
    def topics(self) -> set[str]:
        if self._dirty:
            self._rebuild()
        return self._topics

    @property
    def words(self) -> set[str]:
        if self._dirty:
            self._rebuild()
        return self._words


def analyze_sentence(sentence: str, corpus: SupportCorpus) -> list[Violation]:
    """Unsupported checkable claims in ONE spoken sentence. Questions and
    sentences that disclaim knowledge assert nothing and are never flagged."""
    text = sentence.strip().replace("\u2019", "'")
    if not text or text.endswith("?") or _DISCLAIMER_RE.search(text):
        return []

    violations: list[Violation] = []

    for measure in sorted(extract_measures(text)):
        if not _measure_supported(measure, corpus.measures):
            kind, value = measure
            violations.append(Violation("measure", f"{value:g} {'minutes' if kind == 'time' else 'metres'}"))
    for kind in sorted(_vague_kinds(text)):
        if kind not in corpus.vague_kinds:
            violations.append(Violation("vague_measure", f"vague {kind} with none on file"))
    for minutes in sorted(extract_clock_times(text)):
        if minutes not in corpus.times:
            violations.append(Violation("clock_time", f"{minutes // 60:02d}:{minutes % 60:02d}"))

    if _RELATION_RE.search(text):
        for match in _CAPITALIZED_MID_RE.finditer(text):
            word = match.group(0).lower()
            if word not in _ALWAYS_ALLOWED_CAPITALIZED and word not in corpus.words:
                violations.append(Violation("place", match.group(0)))

    for topic in sorted(extract_topics(text) - corpus.topics):
        violations.append(Violation("topic", topic))

    return violations


def build_static_text(
    properties: list[Any], host_name: str | None = None, guest_name: str | None = None
) -> str:
    """The property details the prompt itself gives the model (name, city,
    check-in/out, capacity, the Lead Agent's portfolio and Locations list) --
    legitimate support for a spoken claim. Deliberately NOT the prompt's rule
    text, which mentions "airport", "beach" etc. in instructions."""
    parts: list[str] = []
    if host_name:
        parts.append(host_name)
    if guest_name:
        parts.append(guest_name)
    for property_ in properties:
        parts.append(
            " ".join(
                str(x)
                for x in (
                    property_.name,
                    getattr(property_, "display_name", None),
                    getattr(property_, "spoken_name", None),
                    property_.city,
                    f"check-in {property_.check_in_time} check-out {property_.check_out_time}",
                    f"sleeps {property_.max_guests}",
                )
                if x
            )
        )
        if getattr(property_, "usp", None):
            parts.append(property_.usp)  # the Locations blurbs the Lead prompt quotes
    return " \n".join(parts)


# --- the processor ----------------------------------------------------------

_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?।][\"')\]]*\s+")
GapSink = Callable[[str, str], Awaitable[None]]


def _miss_line_prefix(miss_line: str) -> str:
    return miss_line.split(",")[0].lower()


class KnowledgeGroundingGuardProcessor(FrameProcessor):
    def __init__(
        self,
        mode: str,
        miss_line: str,
        gap_sink: GapSink | None = None,
        guest_text_provider: Callable[[], str] | None = None,
        last_guest_utterance_provider: Callable[[], str] | None = None,
    ):
        super().__init__()
        self._mode = mode
        self._miss_line = miss_line
        self._gap_sink = gap_sink
        self._guest_text_provider = guest_text_provider
        self._last_guest_utterance_provider = last_guest_utterance_provider
        self.corpus = SupportCorpus()
        self._pending = ""
        self._replaced_this_response = False
        self._last_search_status: str | None = None

    # --- recording (called synchronously from the tool layer) ---

    def set_static_text(self, text: str) -> None:
        self.corpus.set_static(text)

    def record_search(self, status: str, fact_texts: list[str]) -> None:
        self._last_search_status = status
        self.corpus.add_facts(fact_texts)

    def record_tool_outcome(self, tool_name: str, text: str) -> None:
        # search_faq's own result text is instruction wording around the
        # facts, not support -- its facts arrive via record_search.
        if tool_name != "search_faq" and text:
            self.corpus.add_tool_text(text)

    # --- outgoing messages (send_whatsapp) ---

    def check_outgoing(self, text: str) -> list[Violation]:
        """Same checks on text the agent composes to SEND (a WhatsApp body).
        Never raises."""
        try:
            self._refresh_guest_text()
            violations: list[Violation] = []
            for sentence in _split_sentences(text):
                violations.extend(analyze_sentence(sentence, self.corpus))
            return violations
        except Exception:
            logger.exception("KnowledgeGroundingGuard.check_outgoing failed -- allowing the message")
            return []

    @property
    def enforcing(self) -> bool:
        return self._mode == "enforce"

    # --- frame handling ---

    def _refresh_guest_text(self) -> None:
        if self._guest_text_provider is not None:
            try:
                self.corpus.set_guest_text(self._guest_text_provider())
            except Exception:
                pass

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._pending = ""
            self._replaced_this_response = False
            self._refresh_guest_text()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            self._pending += frame.text
            await self._emit_complete_sentences(direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._pending.strip():
                await self._emit_sentence(self._pending, direction)
            self._pending = ""
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _emit_complete_sentences(self, direction: FrameDirection) -> None:
        last_end = 0
        for match in _SENTENCE_BOUNDARY_RE.finditer(self._pending):
            last_end = match.end()
        if last_end == 0:
            return
        complete, self._pending = self._pending[:last_end], self._pending[last_end:]
        start = 0
        for match in _SENTENCE_BOUNDARY_RE.finditer(complete):
            await self._emit_sentence(complete[start : match.end()], direction)
            start = match.end()

    async def _emit_sentence(self, sentence: str, direction: FrameDirection) -> None:
        try:
            await self._emit_checked(sentence, direction)
        except Exception:
            logger.exception("KnowledgeGroundingGuard failed on a sentence -- passing it through unchanged")
            await self.push_frame(LLMTextFrame(sentence), direction)

    async def _emit_checked(self, sentence: str, direction: FrameDirection) -> None:
        if self._mode != "enforce" and self._mode != "shadow":
            await self.push_frame(LLMTextFrame(sentence), direction)
            return

        # Mira saying the not-on-file line after a "possible" result (or
        # without searching) is itself a gap the host should see; a "none"
        # result was already logged by search_faq.
        if _miss_line_prefix(self._miss_line) in sentence.lower().replace("\u2019", "'"):
            if self._last_search_status != "none":
                await self._log_gap(sentence, "miss_line_spoken")
            self._last_search_status = None
            await self.push_frame(LLMTextFrame(sentence), direction)
            return

        violations = analyze_sentence(sentence, self.corpus)
        if not violations:
            await self.push_frame(LLMTextFrame(sentence), direction)
            return

        detail = "; ".join(f"{v.kind}={v.detail}" for v in violations)
        logger.warning(
            "kb_guard_would_block mode={} violations=[{}] sentence={!r}", self._mode, detail, sentence.strip()[:160]
        )
        if self._mode == "shadow":
            await self.push_frame(LLMTextFrame(sentence), direction)
            return

        # enforce: replace with the fixed line once per response, drop the rest.
        if not self._replaced_this_response:
            self._replaced_this_response = True
            await self.push_frame(LLMTextFrame(self._miss_line + " "), direction)
        await self._log_gap(sentence, "guard_blocked")

    async def _log_gap(self, sentence: str, source: str) -> None:
        if self._gap_sink is None:
            return
        question = ""
        if self._last_guest_utterance_provider is not None:
            try:
                question = self._last_guest_utterance_provider()
            except Exception:
                question = ""
        question = (question or sentence).strip()[:500]
        try:
            asyncio.create_task(self._gap_sink(question, source))
        except Exception:
            logger.exception("KnowledgeGroundingGuard could not schedule gap logging")


def _split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text or ""):
        parts.append(text[start : match.end()])
        start = match.end()
    if text and text[start:].strip():
        parts.append(text[start:])
    return parts


# --- reading the guest's words from the LLM context -------------------------


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def guest_text_from_context(context: Any, last_n: int = 6) -> str:
    """The guest's most recent utterances (their own words ground place names
    and times in a reply -- "Delhi" said by the guest is not an invented
    place -- but never a topic: a guest asking about pets doesn't make
    'pets are allowed' true). Empty when the context can't be read."""
    if context is None:
        return ""
    try:
        messages = [m for m in context.messages if isinstance(m, dict) and m.get("role") == "user"]
        return " \n".join(_message_text(m) for m in messages[-last_n:])
    except Exception:
        return ""


def last_guest_utterance(context: Any) -> str:
    text = guest_text_from_context(context, last_n=1)
    return text.strip()
