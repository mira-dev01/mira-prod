"""The eval bot's guard rails: where it may run, and what stands in for STT/TTS.

An eval seeds a host and runs paid LLM calls, so the one mistake that matters is pointing
it at a database that holds a real host's data.
"""

import pytest
from pipecat.frames.frames import TextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import run_test

from app.voice.eval_mode import EvalPassthrough, assert_safe_eval_database, database_name


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("postgresql+asyncpg://mira:mira@localhost:5432/mira_eval", "mira_eval"),
        ("postgresql+asyncpg://u:p@db.example.supabase.co:5432/postgres", "postgres"),
        ("postgresql+asyncpg://u:p@host/neondb?sslmode=require", "neondb"),
        ("postgresql+asyncpg://u:p@host", ""),
    ],
)
def test_database_name_is_the_last_path_segment(url, name):
    assert database_name(url) == name


@pytest.mark.parametrize("name", ["mira_eval", "mira_test", "EVAL", "eval_scratch"])
def test_eval_and_test_databases_are_allowed(name):
    assert_safe_eval_database(f"postgresql+asyncpg://u:p@localhost:5432/{name}")


@pytest.mark.parametrize("name", ["postgres", "neondb", "mira", "railway", ""])
def test_any_other_database_is_refused(name):
    with pytest.raises(RuntimeError, match="Refusing to run the eval bot"):
        assert_safe_eval_database(f"postgresql+asyncpg://u:p@localhost:5432/{name}")


def test_the_escape_hatch_is_explicit(monkeypatch):
    monkeypatch.setenv("EVAL_ALLOW_ANY_DATABASE", "1")
    assert_safe_eval_database("postgresql+asyncpg://u:p@localhost:5432/anything")


@pytest.mark.asyncio
async def test_passthrough_forwards_frames_untouched_and_supports_the_error_handler():
    stand_in = EvalPassthrough(name="EvalSTT")

    # The pipeline attaches this handler to the real STT/TTS; the stand-in must accept it.
    @stand_in.event_handler("on_error")
    async def _on_error(_processor, _error):
        raise AssertionError("never fires")

    down, _ = await run_test(Pipeline([stand_in]), frames_to_send=[TextFrame("hello")])

    assert [f.text for f in down if isinstance(f, TextFrame)] == ["hello"]
