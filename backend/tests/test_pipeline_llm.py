from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService

from app import main as main_mod
from app.voice import pipeline


def test_build_llm_defaults_to_groq_with_low_reasoning_effort(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "llm_provider", "groq")
    monkeypatch.setattr(pipeline.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "groq_models", ["openai/gpt-oss-120b", "llama-3.1-8b-instant"])
    monkeypatch.setattr(main_mod, "llm_health", {})

    llm = pipeline._build_llm()

    assert isinstance(llm, GroqLLMService)
    assert llm._settings.model == "openai/gpt-oss-120b"
    assert llm._settings.extra == {"reasoning_effort": "low"}


def test_build_llm_skips_groq_model_marked_down_in_health_check(monkeypatch):
    # _check_llm_health (app/main.py) marks a model down (e.g. after a 429
    # from that model's own rate limit) -- _build_llm must skip it and pick
    # the next model in settings.groq_models instead of retrying the down one.
    monkeypatch.setattr(pipeline.settings, "llm_provider", "groq")
    monkeypatch.setattr(pipeline.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(
        pipeline.settings, "groq_models", ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]
    )
    monkeypatch.setattr(
        main_mod,
        "llm_health",
        {"openai/gpt-oss-120b": {"ok": False, "latency_s": None, "checked_at": "x", "error": "429"}},
    )

    llm = pipeline._build_llm()

    assert isinstance(llm, GroqLLMService)
    assert llm._settings.model == "llama-3.1-8b-instant"
    # Regression: reasoning_effort is gpt-oss-specific -- llama-3.1-8b-instant
    # rejects it outright with a 400 (confirmed against Groq's live API),
    # which would break the exact fallback call this chain exists to save.
    assert llm._settings.extra == {}


def test_build_llm_falls_back_to_openrouter_when_all_groq_models_down(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "llm_provider", "groq")
    monkeypatch.setattr(pipeline.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "groq_models", ["openai/gpt-oss-120b", "llama-3.1-8b-instant"])
    monkeypatch.setattr(pipeline.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "openrouter_model", "openai/gpt-4.1")
    monkeypatch.setattr(
        main_mod,
        "llm_health",
        {
            "openai/gpt-oss-120b": {"ok": False, "latency_s": None, "checked_at": "x", "error": "429"},
            "llama-3.1-8b-instant": {"ok": False, "latency_s": None, "checked_at": "x", "error": "429"},
        },
    )

    llm = pipeline._build_llm()

    assert isinstance(llm, OpenAILLMService)
    assert llm._settings.model == "openai/gpt-4.1"
    assert "openrouter.ai" in str(llm._client.base_url)


def test_build_llm_anthropic(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(pipeline.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "anthropic_model", "claude-sonnet-4-6")

    llm = pipeline._build_llm()

    assert isinstance(llm, AnthropicLLMService)


def test_build_llm_openrouter_uses_openai_compatible_service_with_openrouter_base_url(monkeypatch):
    # OpenRouter is OpenAI-compatible -- same trick GroqLLMService itself
    # uses (OpenAILLMService pointed at a different base_url), so swapping
    # models (GPT-4.1, Claude, Llama, ...) is just an OPENROUTER_MODEL
    # config change, not new integration code.
    monkeypatch.setattr(pipeline.settings, "llm_provider", "openrouter")
    monkeypatch.setattr(pipeline.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "openrouter_model", "openai/gpt-4.1")

    llm = pipeline._build_llm()

    assert isinstance(llm, OpenAILLMService)
    assert llm._settings.model == "openai/gpt-4.1"
    assert llm._client.base_url is not None
    assert "openrouter.ai" in str(llm._client.base_url)
    # Regression: an unbounded max_tokens request (defaults to 65536) makes
    # OpenRouter's free-tier credit check reject the request outright based
    # on that ceiling, not actual usage -- this must stay capped well below
    # that, while staying generous enough not to truncate a real reply
    # mid-sentence (500 was tried first and was too tight).
    assert llm._settings.max_completion_tokens == 900


def test_build_llm_openrouter_gpt_oss_gets_low_reasoning_effort(monkeypatch):
    # reasoning_effort is a property of gpt-oss itself, not specific to
    # Groq's hosting of it -- the same latency fix must carry over when this
    # model is reached via OpenRouter instead of Groq directly.
    monkeypatch.setattr(pipeline.settings, "llm_provider", "openrouter")
    monkeypatch.setattr(pipeline.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "openrouter_model", "openai/gpt-oss-120b")

    llm = pipeline._build_llm()

    assert llm._settings.extra == {"reasoning_effort": "low"}


def test_build_llm_openrouter_non_gpt_oss_model_has_no_reasoning_effort(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "llm_provider", "openrouter")
    monkeypatch.setattr(pipeline.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "openrouter_model", "anthropic/claude-sonnet-4.6")

    llm = pipeline._build_llm()

    assert llm._settings.extra == {}
