"""Turns a raw scraped Airbnb listing title (Airbnb's own SEO marketing
copy, e.g. "Limon -Cozy forest mornings @ Pause Project 1bhk") into a clean
canonical name a voice agent can actually speak: display_name (dashboard/
context use), spoken_name (what Mira says out loud), plus property_type/
property_style facets extracted along the way.

Two passes:
1. normalize_property_name() -- pure, synchronous, heuristic-only. Never
   raises; any field it can't confidently derive is left None. This alone
   handles the large majority of real listing titles (a small, recurring
   set of delimiters and patterns), with zero added import latency or cost.
2. normalize_property_name_llm_fallback() -- optional second pass, only
   invoked by callers when the heuristic pass reports confidence="low"
   (no recognizable delimiter or property-type word at all). Mirrors
   negotiation_policy_service.parse_negotiation_policy_text's one-shot
   Anthropic -> Groq -> OpenRouter fallback chain. Import-time only (never
   on the live voice-call path); returns None (never raises) on any
   failure or missing provider, same fail-open discipline as
   embedding_service.get_embedding.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.config import settings

logger = logging.getLogger(__name__)

# Public (not underscore-prefixed) -- also reused by
# app/api/v1/properties.py's _brand_candidate(), which needs to split a raw
# title on the same delimiters this module uses, for cross-property brand
# co-occurrence matching.
NAME_DELIMITER_RE = re.compile(r"\s*[|·]\s*|\s+-\s*|\s*-\s+")

_BHK_RE = re.compile(r"(\d+)\s*bhk", re.IGNORECASE)
_COUNT_RE = re.compile(r"(\d+)\s*[- ]?\s*(bedroom|bed|bathroom|br|ba)s?\b", re.IGNORECASE)
_STAR_RATING_RE = re.compile(r"★\s*\d+(\.\d+)?")
_EXTRA_WHITESPACE_RE = re.compile(r"\s{2,}")

# Ordered so a longer/more specific phrase matches before a shorter
# substring of it (e.g. "farmhouse" before "house").
_PROPERTY_TYPE_WORDS = [
    "glasshouse",
    "glass house",
    "farmhouse",
    "treehouse",
    "houseboat",
    "bungalow",
    "cottage",
    "villa",
    "cabin",
    "chalet",
    "studio",
    "homestay",
    "apartment",
    "suite",
]

# Generic marketing filler that adds no signal to a display name once the
# structured bits (bhk count, property type, brand) are already pulled out.
_FILLER_PHRASES = [
    "best deal",
    "top rated",
    "guest favorite",
    "superhost",
]

_MAX_SPOKEN_WORDS = 3

# Must match Property column widths (app/models/property.py) -- a long,
# unsplit SEO-stuffed title (this feature's actual target input) or an
# unconstrained LLM-fallback response can otherwise exceed any of these
# columns and fail the import with a DB-level StringDataRightTruncation
# error.
_DISPLAY_NAME_MAX_LEN = 120
_SPOKEN_NAME_MAX_LEN = 60
_PROPERTY_TYPE_MAX_LEN = 60
_PROPERTY_STYLE_MAX_LEN = 80
_BRAND_MAX_LEN = 80


def _truncate_at_word_boundary(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0].strip(" -·|,")
    # A single word longer than max_len (or empty after rsplit) has no
    # safe word boundary to cut at -- hard-truncate rather than return "".
    return truncated if truncated else text[:max_len]

_EXTRACTION_PROMPT = """You clean up a short-term rental listing's raw, SEO-stuffed marketing title \
into a name a voice assistant can say naturally on a phone call.

Given the raw title (and description, if provided), extract:
- display_name: a short, clean, human-presentable name (may keep a light descriptor, e.g. "Pine \
Glasshouse Suite"). Strip star ratings, bedroom/bathroom counts, brand names, and generic marketing \
filler ("best deal", "top rated", etc).
- spoken_name: an even shorter name (1-3 words) a host or guest would actually say out loud in \
conversation, e.g. "Pine". If nothing shorter is safely extractable, repeat display_name.
- property_type: a coarse category if identifiable (villa, cottage, apartment, cabin, glasshouse, \
studio, etc), else null.
- property_style: a descriptive style/vibe distinct from type if identifiable (e.g. "beachfront", \
"forest cabin", "rooftop"), else null.
- brand: a recurring multi-property brand/collection name mentioned in the title (e.g. "Pause \
Project"), else null.

Never invent anything not implied by the title/description. If you can't confidently extract a field, \
use null for it.

Respond with ONLY a JSON object of this exact shape, no other text:
{"display_name": "...", "spoken_name": "...", "property_type": null, "property_style": null, "brand": null}

Raw title:
"""


@dataclass
class NormalizedName:
    display_name: str | None = None
    spoken_name: str | None = None
    property_type: str | None = None
    property_style: str | None = None
    brand: str | None = None
    bedroom_count: int | None = None
    # bathroom_count is extracted for symmetry with bedroom_count (same
    # source patterns, "N bathroom"/"N bath") but Property has no matching
    # column today -- no pitch/filter feature currently consumes it. Kept
    # on this dataclass rather than dropped since the extraction is free
    # and a future feature (e.g. "does it have 2 bathrooms") would want it
    # without redoing this parsing.
    bathroom_count: int | None = None
    confidence: Literal["high", "low"] = "low"


def _extract_counts(segment: str) -> tuple[int | None, int | None, str]:
    """Pulls a bedroom/bathroom count out of `segment` (bhk-style or
    "N bedroom"/"N bathroom" style) and returns (bedrooms, bathrooms,
    segment_with_count_removed)."""
    bedrooms = bathrooms = None

    bhk_match = _BHK_RE.search(segment)
    if bhk_match:
        bedrooms = int(bhk_match.group(1))
        segment = _BHK_RE.sub("", segment)

    for match in _COUNT_RE.finditer(segment):
        count, label = int(match.group(1)), match.group(2).lower()
        if label in ("bedroom", "bed", "br") and bedrooms is None:
            bedrooms = count
        elif label in ("bathroom", "ba") and bathrooms is None:
            bathrooms = count
    segment = _COUNT_RE.sub("", segment)

    return bedrooms, bathrooms, segment


def _extract_property_type(segment: str) -> tuple[str | None, str]:
    lowered = segment.lower()
    for type_word in _PROPERTY_TYPE_WORDS:
        if type_word in lowered:
            # Also swallow a hyphen directly glued to the type word on
            # either side (e.g. "Villa-private pool", no delimiting
            # whitespace at all) so it doesn't linger as a stray "-" in
            # the cleaned segment.
            pattern = re.compile(r"-?\s*" + re.escape(type_word) + r"\s*-?", re.IGNORECASE)
            segment = pattern.sub(" ", segment)
            return type_word, segment
    return None, segment


def _clean_display_segment(segment: str) -> str:
    segment = _STAR_RATING_RE.sub("", segment)
    lowered = segment.lower()
    for filler in _FILLER_PHRASES:
        if filler in lowered:
            pattern = re.compile(re.escape(filler), re.IGNORECASE)
            segment = pattern.sub("", segment)
    segment = segment.strip(" -·|,")
    segment = _EXTRA_WHITESPACE_RE.sub(" ", segment).strip()
    return segment


def _derive_spoken_name(display_name: str) -> str:
    words = display_name.split()
    if len(words) <= _MAX_SPOKEN_WORDS:
        return display_name

    lowered_words = [w.lower() for w in words]
    for type_word in _PROPERTY_TYPE_WORDS:
        type_tokens = type_word.split()
        for i in range(len(lowered_words) - len(type_tokens) + 1):
            if lowered_words[i : i + len(type_tokens)] == type_tokens and i > 0:
                candidate = " ".join(words[:i]).strip(" -·|,")
                if candidate and len(candidate.split()) <= _MAX_SPOKEN_WORDS:
                    return candidate

    # Nothing safely shorter found -- never guess a truncation that could
    # read as a broken sentence fragment; speak the full display_name.
    return display_name


def normalize_property_name(raw_name: str, *, description: str | None = None) -> NormalizedName:
    if not raw_name or not raw_name.strip():
        return NormalizedName()

    segments = [s for s in NAME_DELIMITER_RE.split(raw_name) if s.strip()]
    delimiter_found = len(segments) > 1
    if not segments:
        segments = [raw_name]

    bedroom_count = bathroom_count = None
    property_type: str | None = None
    cleaned_segments: list[str] = []

    for segment in segments:
        bedrooms, bathrooms, segment = _extract_counts(segment)
        if bedrooms is not None and bedroom_count is None:
            bedroom_count = bedrooms
        if bathrooms is not None and bathroom_count is None:
            bathroom_count = bathrooms

        type_word, segment = _extract_property_type(segment)
        if type_word and property_type is None:
            property_type = type_word

        cleaned = _clean_display_segment(segment)
        if cleaned:
            cleaned_segments.append(cleaned)

    # brand is deliberately NOT resolved here -- it needs cross-property
    # lookup for the same host (see _upsert_property_from_parsed), which
    # this pure, single-title function has no access to.

    property_type_found = property_type is not None

    if not cleaned_segments:
        display_name = None
        spoken_name = None
    elif len(cleaned_segments) == 1:
        # No real delimiter split anything usable apart -- the one
        # remaining segment is both the display and (if short enough)
        # spoken candidate.
        display_name = cleaned_segments[0]
        spoken_name = _derive_spoken_name(display_name)
    else:
        # A real title with multiple delimited segments follows Airbnb's
        # own convention closely: the FIRST segment is the actual proper-
        # noun listing name ("Pine", "Nile", "Mocha", "Terra"); later
        # segments are descriptive/brand text. Confirmed against real
        # titles this normalizer targets -- picking the *longest* segment
        # instead (an earlier version of this function) wrongly picked
        # the description over the name (e.g. "Suite w/bathtub" instead
        # of "Pine"). spoken_name is just that first segment; display_name
        # pairs it with the next segment as a short descriptor so some of
        # the distinguishing detail (e.g. "Suite w/bathtub") isn't lost
        # entirely from written/dashboard contexts.
        spoken_name = cleaned_segments[0]
        if len(spoken_name.split()) > _MAX_SPOKEN_WORDS:
            spoken_name = _derive_spoken_name(spoken_name)
        descriptor = cleaned_segments[1]
        display_name = f"{cleaned_segments[0]} - {descriptor}" if descriptor else cleaned_segments[0]

    confidence: Literal["high", "low"] = "high" if (delimiter_found or property_type_found) else "low"

    if display_name:
        display_name = _truncate_at_word_boundary(display_name, _DISPLAY_NAME_MAX_LEN)
    if spoken_name:
        spoken_name = _truncate_at_word_boundary(spoken_name, _SPOKEN_NAME_MAX_LEN)

    return NormalizedName(
        display_name=display_name,
        spoken_name=spoken_name,
        property_type=property_type,
        property_style=None,
        brand=None,
        bedroom_count=bedroom_count,
        bathroom_count=bathroom_count,
        confidence=confidence,
    )


def _parse_llm_json(raw_text: str) -> NormalizedName | None:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.exception("property_normalizer LLM fallback returned invalid JSON")
        return None

    if not isinstance(parsed, dict):
        return None

    def _clean_str(value) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    display_name = _clean_str(parsed.get("display_name"))
    spoken_name = _clean_str(parsed.get("spoken_name")) or display_name
    property_type = _clean_str(parsed.get("property_type"))
    property_style = _clean_str(parsed.get("property_style"))
    brand = _clean_str(parsed.get("brand"))
    if display_name:
        display_name = _truncate_at_word_boundary(display_name, _DISPLAY_NAME_MAX_LEN)
    if spoken_name:
        spoken_name = _truncate_at_word_boundary(spoken_name, _SPOKEN_NAME_MAX_LEN)
    if property_type:
        property_type = _truncate_at_word_boundary(property_type, _PROPERTY_TYPE_MAX_LEN)
    if property_style:
        property_style = _truncate_at_word_boundary(property_style, _PROPERTY_STYLE_MAX_LEN)
    if brand:
        brand = _truncate_at_word_boundary(brand, _BRAND_MAX_LEN)

    return NormalizedName(
        display_name=display_name,
        spoken_name=spoken_name,
        property_type=property_type,
        property_style=property_style,
        brand=brand,
        confidence="high",
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
        max_tokens=512,
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


async def normalize_property_name_llm_fallback(raw_name: str, description: str | None = None) -> NormalizedName | None:
    """Only called by import-time callers when the heuristic pass above
    reports confidence="low". Never raises -- returns None on any failure
    or if no provider is configured, exactly like embedding_service's
    get_embedding, so a bad/unreachable LLM never fails an import."""
    prompt = _EXTRACTION_PROMPT + raw_name
    if description:
        prompt += f"\n\nDescription:\n{description[:500]}"

    try:
        if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
            raw = await _call_anthropic(prompt)
        elif settings.groq_api_key:
            raw = await _call_groq(prompt)
        elif settings.openrouter_api_key:
            raw = await _call_openrouter(prompt)
        else:
            return None
    except Exception:
        logger.exception("property_normalizer LLM fallback call failed for raw_name=%r", raw_name[:80])
        return None

    return _parse_llm_json(raw)
