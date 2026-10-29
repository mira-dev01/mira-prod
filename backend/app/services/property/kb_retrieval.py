"""Knowledge-base retrieval behind the search_faq voice tool.

search_faq's contract is: the agent may speak ONLY what this module returns.
So this module's job is to find the small set of stored facts (see
chunking.build_fact_chunks -- verified FAQ rows, the property's own fields,
active seasonal notes) that actually bear on the guest's question, and to say
honestly when there are none. It never returns "everything we know" as a
fallback -- handing the model the whole blob is what let it answer a distance
question from its own head.

Three outcomes (KbSearchResult.status):
  found     at least one fact clearly matches (lexical coverage or embedding).
  possible  something loosely matches, or a clear match exists but the guest
            named something (a place) that appears nowhere on file. The model
            is told these may not answer the question; the grounding guard
            (app/voice/kb_grounding_guard.py) verifies what it then says.
  none      nothing relevant. search_faq logs a gap for the host.

Matching is hybrid: in-Python lexical coverage over the property's facts (a
property has tens of facts, so this is free), plus a query embedding compared
against precomputed fact embeddings only when lexical coverage is not already
convincing. The embedding call has a hard timeout and any failure degrades to
lexical-only -- it can never degrade to model knowledge.
"""

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.services import embedding_service
from app.services.property.chunking import FACT_CHUNK_TYPE, build_fact_chunks
from app.utils.dates import IST

KbStatus = Literal["found", "possible", "none"]

_MAX_FACTS_RETURNED = 4
_FACT_CACHE_TTL_SECONDS = 60.0
_LAZY_SYNC_COOLDOWN_SECONDS = 300.0


@dataclass(frozen=True)
class Fact:
    id: uuid.UUID | None
    text: str
    source_kind: str
    source_ref: str | None = None
    meta: dict | None = None
    embedding: list | None = None


@dataclass
class KbSearchResult:
    status: KbStatus
    facts: list[Fact] = field(default_factory=list)
    # Capitalized words the guest used that appear nowhere on file (e.g.
    # "Delhi"): handed to the model explicitly so it cannot fill the gap.
    not_on_file_terms: list[str] = field(default_factory=list)
    used_embedding: bool = False


# --- tokenization -----------------------------------------------------------

_STOPWORDS = frozenset(
    """
    a an the and or but if then so of to in on at by for from with about into over under near nearby far away
    is are was were be been being am do does did done have has had having can could will would shall should may might
    i me my we our us you your he she it its they them their this that these those there here
    what which who whom whose when where why how much many any some more most very too also just only
    please tell know let want like need get got give say said ask asked asking question questions
    yes no not ok okay hello hi hey sir madam ji
    property place villa apartment flat home house room stay staying booking available availability allowed allow
    permitted possible provide provided offer offered offers include included includes including whether
    distance distances kilometer kilometre km minute minutes hour hours drive walk walking reach reachable
    kya hai hain ka ke ki ko se me mein par aur ya hum aap tum mujhe
    """.split()
)

# Surface forms that mean the same thing for matching purposes. A token maps
# to its group's first word; both the query and the facts are mapped, so
# "internet" in a question matches "WiFi" on file and vice versa.
_SYNONYM_GROUPS = [
    ("wifi", "wi", "fi", "internet", "broadband"),
    ("pool", "swimming", "jacuzzi"),
    ("pet", "pets", "dog", "dogs", "cat", "cats"),
    ("ac", "aircon", "conditioning", "conditioner", "cooling"),
    ("parking", "park", "garage"),
    ("kitchen", "kitchenette", "cooking", "cook", "stove"),
    ("airport", "flight", "flights"),
    ("station", "railway", "train", "trains"),
    ("smoking", "smoke", "smoker", "smokers", "cigarette", "cigarettes"),
    ("alcohol", "liquor", "drinks", "drinking", "booze", "beer", "wine"),
    ("party", "parties", "loud", "events", "gathering", "gatherings"),
    ("visitor", "visitors", "outsider", "outsiders"),
    ("cancel", "cancellation", "cancelled", "refund", "refunds"),
    ("checkin", "check", "arrival", "arrive", "arriving"),
    ("checkout", "departure", "depart", "leaving"),
    ("food", "meal", "meals", "breakfast", "lunch", "dinner", "restaurant", "restaurants", "cafe", "cafes", "dhaba"),
    ("cab", "taxi", "uber", "ola", "cabs", "taxis"),
    ("bike", "scooter", "scooters", "bikes", "motorbike", "rental"),
    ("heater", "heating", "geyser", "hot"),
    ("guest", "people", "person", "persons", "adults", "capacity", "occupancy", "accommodate", "sleep"),
]
_SYNONYM_TO_CANONICAL = {word: group[0] for group in _SYNONYM_GROUPS for word in group}

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _canonical(token: str) -> str:
    token = token.lower()
    return _SYNONYM_TO_CANONICAL.get(token) or _SYNONYM_TO_CANONICAL.get(_stem(token)) or _stem(token)


def tokenize(text: str) -> list[str]:
    return [_canonical(match.group(0)) for match in _TOKEN_RE.finditer(text or "")]


def content_tokens(text: str) -> list[str]:
    """The query's meaningful words: stopwords/fillers dropped, synonyms and
    plurals collapsed, order kept, duplicates removed."""
    seen: set[str] = set()
    result: list[str] = []
    for match in _TOKEN_RE.finditer(text or ""):
        raw = match.group(0).lower()
        if raw in _STOPWORDS:
            continue
        token = _canonical(raw)
        if token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_GENERIC_CAPITALIZED = frozenset(
    {"mira", "namaste", "hindi", "english", "hinglish", "whatsapp", "airbnb", "monday", "tuesday", "wednesday",
     "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december", "india", "indian"}
)


def _query_entities(query: str) -> list[str]:
    """Capitalized words in the question that aren't its first word or a
    common proper noun -- the places/names a guest is asking about."""
    words = list(_CAPITALIZED_RE.finditer(query or ""))
    entities: list[str] = []
    for match in words:
        if match.start() == 0:
            continue
        word = match.group(0)
        if word.lower() in _GENERIC_CAPITALIZED or word.lower() in _STOPWORDS:
            continue
        if word not in entities:
            entities.append(word)
    return entities


# --- fact loading -----------------------------------------------------------

_fact_cache: dict[uuid.UUID, tuple[float, list[Fact]]] = {}
_lazy_sync_started: dict[uuid.UUID, float] = {}


def invalidate_fact_cache(property_id: uuid.UUID | None = None) -> None:
    if property_id is None:
        _fact_cache.clear()
    else:
        _fact_cache.pop(property_id, None)


def _season_active(meta: dict | None, month: int) -> bool:
    if not meta:
        return True
    start, end = meta.get("start_month"), meta.get("end_month")
    if start is None or end is None:
        return True
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end


def _fact_from_row(row: PropertyChunk) -> Fact:
    return Fact(
        id=row.id,
        text=row.text,
        source_kind=row.source_kind or "",
        source_ref=row.source_ref,
        meta=row.meta,
        embedding=row.embedding,
    )


async def _schedule_lazy_sync(property_id: uuid.UUID) -> None:
    """Rebuilds a property's persisted fact rows in the background, using its
    own session. Rate-limited per property so a burst of questions can't
    start a pile of identical syncs."""
    now = time.monotonic()
    if now - _lazy_sync_started.get(property_id, -1e9) < _LAZY_SYNC_COOLDOWN_SECONDS:
        return
    _lazy_sync_started[property_id] = now

    async def _run() -> None:
        from app.database import AsyncSessionLocal
        from app.services.property.chunking import sync_property_facts

        try:
            async with AsyncSessionLocal() as session:
                fresh = await session.get(Property, property_id)
                if fresh is not None:
                    await sync_property_facts(session, fresh)
        except Exception:
            logger.exception("kb_fact_lazy_sync_failed property_id={}", property_id)

    asyncio.create_task(_run())


async def load_property_facts(db: AsyncSession, property_: Property) -> list[Fact]:
    """The property's facts (cached ~60s). If none are persisted yet, or the
    property / its FAQ rows were edited after they were built, the facts are
    rebuilt in memory right now from the live data (lexical-only until the
    background sync re-embeds them) -- so a property is never answered from a
    stale or missing fact set, and deploying this before backfilling every
    property can't regress anyone."""
    cached = _fact_cache.get(property_.id)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    rows = list(
        (
            await db.scalars(
                select(PropertyChunk).where(
                    PropertyChunk.property_id == property_.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE
                )
            )
        ).all()
    )
    stale = not rows
    if rows:
        newest_fact: datetime = max(row.created_at for row in rows)
        newest_faq = await db.scalar(
            select(func.max(FaqEntry.updated_at)).where(FaqEntry.property_id == property_.id)
        )
        # Read explicitly rather than via property_.updated_at: on an expired
        # ORM instance that attribute access would try to lazy-load (IO) from
        # inside async code.
        property_updated_at = await db.scalar(select(Property.updated_at).where(Property.id == property_.id))
        if (property_updated_at and property_updated_at > newest_fact) or (newest_faq and newest_faq > newest_fact):
            stale = True

    if stale:
        entries = list(
            (
                await db.scalars(
                    select(FaqEntry).where(FaqEntry.property_id == property_.id, FaqEntry.status == "verified")
                )
            ).all()
        )
        facts = [
            Fact(None, draft.text, draft.source_kind, draft.source_ref, draft.meta, None)
            for draft in build_fact_chunks(property_, entries)
        ]
        await _schedule_lazy_sync(property_.id)
    else:
        facts = [_fact_from_row(row) for row in rows]

    _fact_cache[property_.id] = (time.monotonic() + _FACT_CACHE_TTL_SECONDS, facts)
    return facts


async def load_portfolio_facts(db: AsyncSession, user_id: uuid.UUID) -> list[Fact]:
    """Verified FAQ rows that aren't tied to one property (property_id NULL),
    which apply across the host's portfolio. Read live -- they're few and a
    host edit should take effect on the next question."""
    entries = list(
        (
            await db.scalars(
                select(FaqEntry)
                .where(FaqEntry.user_id == user_id, FaqEntry.property_id.is_(None), FaqEntry.status == "verified")
                .limit(200)
            )
        ).all()
    )
    return [
        Fact(entry.id, f"Q: {entry.question} A: {entry.answer}", "faq_entry", str(entry.id), None, entry.question_embedding)
        for entry in entries
    ]


# --- search -----------------------------------------------------------------


def _lexical_score(query_tokens: list[str], fact: Fact) -> tuple[float, int]:
    fact_tokens = set(tokenize(fact.text))
    matched = sum(1 for token in query_tokens if token in fact_tokens)
    return (matched / len(query_tokens) if query_tokens else 0.0), matched


async def _embed_query(query: str) -> list[float] | None:
    try:
        return await asyncio.wait_for(
            embedding_service.get_embedding(query), timeout=settings.kb_embedding_timeout_seconds
        )
    except (asyncio.TimeoutError, Exception):
        logger.warning("kb_query_embedding_unavailable -- continuing lexical-only")
        return None


async def search_facts(
    query: str, property_facts: list[Fact], portfolio_facts: list[Fact], vocabulary_extra: str = ""
) -> KbSearchResult:
    """Finds the facts that bear on `query`. `vocabulary_extra` is text that
    legitimately exists on file but isn't a fact (the property's name and
    city), so a guest naming the property isn't treated as asking about
    something unknown."""
    month = datetime.now(IST).month
    candidates = [f for f in property_facts if f.source_kind != "seasonal_note" or _season_active(f.meta, month)]
    candidates += portfolio_facts

    if not candidates:
        return KbSearchResult("none", not_on_file_terms=_query_entities(query))

    query_tokens = content_tokens(query)
    if not query_tokens:
        # "Tell me about this place" / "what's it like": no specific topic,
        # so the overview facts are the right answer.
        overview = [f for f in candidates if f.source_kind in ("description", "structured")][:_MAX_FACTS_RETURNED]
        return KbSearchResult("found" if overview else "none", overview)

    vocabulary = {token for fact in candidates for token in tokenize(fact.text)} | set(tokenize(vocabulary_extra))
    not_on_file = [e for e in _query_entities(query) if _canonical(e) not in vocabulary]

    scored: list[tuple[float, float, int, Fact]] = []
    for fact in candidates:
        coverage, matched = _lexical_score(query_tokens, fact)
        scored.append((coverage, 0.0, matched, fact))

    def _is_found(coverage: float, cosine: float, matched: int) -> bool:
        return (
            coverage >= settings.kb_found_coverage and matched >= min(2, len(query_tokens))
        ) or cosine >= settings.kb_found_cosine

    used_embedding = False
    if not any(_is_found(c, 0.0, m) for c, _, m, _ in scored) and any(f.embedding for f in candidates):
        query_embedding = await _embed_query(query)
        if query_embedding is not None:
            used_embedding = True
            scored = [
                (
                    coverage,
                    embedding_service.cosine_similarity(query_embedding, fact.embedding) if fact.embedding else 0.0,
                    matched,
                    fact,
                )
                for coverage, _, matched, fact in scored
            ]

    found = [s for s in scored if _is_found(s[0], s[1], s[2])]
    possible = [
        s
        for s in scored
        if s not in found and ((s[0] >= settings.kb_possible_coverage and s[2] >= 1) or s[1] >= settings.kb_possible_cosine)
    ]

    def _rank(item: tuple[float, float, int, Fact]) -> float:
        return item[0] + item[1]

    if found:
        chosen = sorted(found, key=_rank, reverse=True)[:_MAX_FACTS_RETURNED]
        # A clear match exists, but the guest also named something that is
        # nowhere on file: the match may well be about something else.
        status: KbStatus = "possible" if not_on_file else "found"
        return KbSearchResult(status, [s[3] for s in chosen], not_on_file, used_embedding)
    if possible:
        chosen = sorted(possible, key=_rank, reverse=True)[:_MAX_FACTS_RETURNED]
        return KbSearchResult("possible", [s[3] for s in chosen], not_on_file, used_embedding)
    return KbSearchResult("none", [], not_on_file, used_embedding)


# --- rendering --------------------------------------------------------------

_FOUND_HEADER = (
    "KNOWLEDGE BASE FACTS for {name} (the only source you may answer from -- paraphrase or translate freely but add "
    "nothing: no estimates, no rounding, no general knowledge. If these facts do not state the specific answer to "
    "what the guest asked, follow the KNOWLEDGE BOUNDARY rule instead of answering):"
)
_POSSIBLE_HEADER = (
    "POSSIBLY RELEVANT FACTS for {name} -- these may NOT answer the guest's question. Use one only if it explicitly "
    "states the answer; otherwise follow the KNOWLEDGE BOUNDARY rule (say the not-on-file line, never guess):"
)


def render_kb_result(property_name: str, result: KbSearchResult, miss_line: str) -> str:
    if result.status == "none":
        text = (
            "I don't have verified information about that on file. "
            f'Say "{miss_line}" in the guest\'s language and follow the KNOWLEDGE BOUNDARY rule for the escalation. '
            "Do not answer from general knowledge."
        )
    else:
        header = (_FOUND_HEADER if result.status == "found" else _POSSIBLE_HEADER).format(name=property_name)
        text = header + "\n" + "\n".join(f"{i}. {fact.text}" for i, fact in enumerate(result.facts, 1))
    if result.not_on_file_terms:
        terms = ", ".join(f'"{t}"' for t in result.not_on_file_terms)
        text += (
            f"\nNOT ON FILE: {terms} appear nowhere in this property's information -- you cannot say anything "
            "about how it relates to this property (distance, travel time, nearness)."
        )
    return text
