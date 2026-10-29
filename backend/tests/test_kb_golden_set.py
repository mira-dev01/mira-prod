"""Lexical-only regression over the golden question set (tests/kb_golden_set.py).
Runs with no embedding key. The cosine thresholds are tuned separately with
scripts/tune_kb_retrieval.py."""

import uuid

import pytest

from app.models.property import Property
from app.services import embedding_service
from app.services.property import kb_retrieval
from app.services.property.chunking import build_fact_chunks
from tests.kb_golden_set import GOLDEN_CASES, GOLDEN_PROPERTY


@pytest.fixture(scope="module")
def facts():
    prop = Property(id=uuid.uuid4(), user_id=uuid.uuid4(), **GOLDEN_PROPERTY)
    return [kb_retrieval.Fact(None, d.text, d.source_kind, d.source_ref, d.meta, None) for d in build_fact_chunks(prop, [])]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    async def _none(text):
        return None

    monkeypatch.setattr(embedding_service, "get_embedding", _none)


@pytest.mark.parametrize("question,expected", [c for c in GOLDEN_CASES if c[1] is not None])
async def test_on_file_question_finds_its_fact(facts, question, expected):
    result = await kb_retrieval.search_facts(question, facts, [], "Sunset Villa Manali")
    assert result.status in ("found", "possible"), result
    assert expected.lower() in " ".join(f.text for f in result.facts).lower()


@pytest.mark.parametrize("question", [c[0] for c in GOLDEN_CASES if c[1] is None])
async def test_not_on_file_question_is_never_a_confident_match(facts, question):
    result = await kb_retrieval.search_facts(question, facts, [], "Sunset Villa Manali")
    assert result.status != "found", f"{question!r} wrongly matched: {[f.text for f in result.facts]}"
