"""Measures knowledge-base retrieval quality on tests/kb_golden_set.py and
suggests KB_FOUND_COSINE / KB_POSSIBLE_COSINE.

For each golden question it reports, with the CURRENT thresholds:
  recall        on-file questions whose expected fact came back (found/possible)
  confident     on-file questions answered at status "found" (vs only "possible")
  false-found   NOT-on-file questions wrongly returned as a confident "found"
and then sweeps the found-cosine threshold to show the trade-off.

Needs a working OPENROUTER_API_KEY (embeddings); without one it measures the
lexical-only baseline and says so.

Run (from backend/):  python -m scripts.tune_kb_retrieval
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models.property import Property  # noqa: E402
from app.services import embedding_service  # noqa: E402
from app.services.property import kb_retrieval  # noqa: E402
from app.services.property.chunking import build_fact_chunks  # noqa: E402
from tests.kb_golden_set import GOLDEN_CASES, GOLDEN_PROPERTY  # noqa: E402


async def _facts():
    prop = Property(id=uuid.uuid4(), user_id=uuid.uuid4(), **GOLDEN_PROPERTY)
    drafts = build_fact_chunks(prop, [])
    embeddings = [await embedding_service.get_embedding(d.text) for d in drafts]
    facts = [
        kb_retrieval.Fact(None, d.text, d.source_kind, d.source_ref, d.meta, e) for d, e in zip(drafts, embeddings)
    ]
    return facts, any(e is not None for e in embeddings)


async def _evaluate(facts):
    on_file = misses = recalled = confident = false_found = 0
    failures = []
    for question, expected in GOLDEN_CASES:
        result = await kb_retrieval.search_facts(question, facts, [], "Sunset Villa Manali")
        text = " ".join(f.text for f in result.facts)
        if expected is None:
            misses += 1
            if result.status == "found":
                false_found += 1
                failures.append(f"FALSE-FOUND  {question!r}: {text[:90]}")
        else:
            on_file += 1
            if result.status != "none" and expected.lower() in text.lower():
                recalled += 1
                if result.status == "found":
                    confident += 1
            else:
                failures.append(f"MISSED       {question!r} (status={result.status}) expected {expected!r}")
    return on_file, misses, recalled, confident, false_found, failures


async def main() -> None:
    facts, have_embeddings = await _facts()
    if not have_embeddings:
        print("!! No embeddings available (OPENROUTER_API_KEY missing/invalid): measuring LEXICAL-ONLY baseline.\n")
    on_file, misses, recalled, confident, false_found, failures = await _evaluate(facts)
    print(f"thresholds: found_cosine={settings.kb_found_cosine} possible_cosine={settings.kb_possible_cosine} "
          f"found_coverage={settings.kb_found_coverage} possible_coverage={settings.kb_possible_coverage}")
    print(f"recall        {recalled}/{on_file}")
    print(f"confident     {confident}/{on_file}")
    print(f"false-found   {false_found}/{misses}   <- must be 0")
    for line in failures:
        print("  ", line)

    if have_embeddings:
        print("\nsweep of kb_found_cosine (possible = found - 0.13):")
        original = (settings.kb_found_cosine, settings.kb_possible_cosine)
        for found in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
            settings.kb_found_cosine, settings.kb_possible_cosine = found, found - 0.13
            _, _, rec, conf, ff, _ = await _evaluate(facts)
            print(f"  found={found:.2f}: recall {rec}/{on_file}  confident {conf}/{on_file}  false-found {ff}/{misses}")
        settings.kb_found_cosine, settings.kb_possible_cosine = original
        print("\nPick the LOWEST found threshold with false-found 0, then set KB_FOUND_COSINE / KB_POSSIBLE_COSINE.")


if __name__ == "__main__":
    asyncio.run(main())
