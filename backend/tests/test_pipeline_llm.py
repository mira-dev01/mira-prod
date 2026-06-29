from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.openai.llm import OpenAILLMService

from app.voice import pipeline


def test_build_llm_defaults_to_groq_with_low_reasoning_effort(monkeypatch):
    monkeypatch.setattr(pipeline.settings, "llm_provider", "groq")
    monkeypatch.setattr(pipeline.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(pipeline.settings, "groq_model", "openai/gpt-oss-120b")

    llm = pipeline._build_llm()

    assert isinstance(llm, GroqLLMService)
    assert llm._settings.model == "openai/gpt-oss-120b"
    assert llm._settings.extra == {"reasoning_effort": "low"}


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
