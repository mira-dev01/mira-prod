"""The judge LLM for `eval:` expectations: a Groq model, so the suite needs no new provider
or key (pipecat's own judge only knows ollama and openai; it accepts a factory for the rest).

Referenced from scenarios as `judge.eval.factory: eval.judge_llm.make_judge_llm`.
"""

from pipecat.services.groq.llm import GroqLLMService

from app.config import settings

# Not the model the bot itself runs on (gpt-oss-120b): a model grading its own family's replies
# is a weaker check. Tried 2026-11-14 on a known-good and a known-bad reply: qwen3.8-27b and
# gpt-oss-120b judged both correctly, gpt-oss-20b failed the good one.
DEFAULT_JUDGE_MODEL = "qwen/qwen3.8-27b"


def make_judge_llm(config: dict) -> GroqLLMService:
    return GroqLLMService(
        api_key=settings.groq_api_key,
        settings=GroqLLMService.Settings(model=config.get("model") or DEFAULT_JUDGE_MODEL, temperature=0),
    )
