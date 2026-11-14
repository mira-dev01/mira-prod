"""The Mira Lead Agent as the pipecat eval harness drives it.

    pipecat eval suite eval/manifest.yaml        # spawns this once per scenario
    python scripts/run_eval_bot.py -t eval --port 7900   # by hand, then `pipecat eval run ... --bot-url ws://localhost:7900`

Same prompt builders, LLM service, tool wrappers and guard chain as a live call
(app/voice/pipeline.py's `run_lead_pipeline_on_transport`); only the transport, the
absent STT/TTS and the skipped post-call steps differ (app/voice/eval_mode.py).

It talks to an EVAL database, never a real host's: `DATABASE_URL` defaults to the
local `mira_eval`, and the bot refuses to start against a database whose name does
not contain "eval" or "test". The seeded host is `eval/seed.py`.

Text-mode scenarios send `send-text`, which the RTVI processor appends to the LLM
context directly. So they exercise the prompt, the tools, the state sync and the
output guards, but NOT the speech-to-text transcript path (low-confidence guard,
language sync): those are covered by unit tests, and by audio-mode scenarios once
this suite grows them.
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Before anything imports app.config: the .env in backend/ holds real credentials and a
# production ENVIRONMENT, and an eval must not pick up either its database or its mode.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://mira:mira@localhost:5432/mira_eval")
os.environ.setdefault("ENVIRONMENT", "development")
# Nothing in an eval may reach real guests or paid telephony.
os.environ.setdefault("TWILIO_ENABLED", "false")

from loguru import logger  # noqa: E402
from pipecat.runner.types import RunnerArguments  # noqa: E402
from pipecat.runner.utils import create_transport  # noqa: E402
from pipecat.evals.transport import EvalTransportParams  # noqa: E402

from app.config import settings  # noqa: E402
from app.voice.eval_mode import assert_safe_eval_database  # noqa: E402

transport_params = {
    # Text mode: no audio in or out. An audio-mode scenario turns the virtual mic on per connection.
    "eval": lambda: EvalTransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def bot(runner_args: RunnerArguments):
    assert_safe_eval_database(settings.database_url)

    from eval.seed import ensure_eval_host
    from app.voice.pipeline import run_lead_pipeline_on_transport

    host = await ensure_eval_host()
    transport = await create_transport(runner_args, transport_params)
    logger.info("eval_bot_ready host={}", host.email)
    await run_lead_pipeline_on_transport(transport, host, eval_mode=True)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
