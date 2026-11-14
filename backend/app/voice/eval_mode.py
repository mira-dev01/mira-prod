"""What the pipeline does differently when it is driven by the eval harness
(`backend/scripts/run_eval_bot.py`, pipecat.evals), and nothing else.

The eval bot runs the same prompt, LLM service, tool wrappers and guard chain as a
real call. Three things cannot be the same, because the harness is not a phone:

  * there is no audio, so speech-to-text and text-to-speech are stood in for by
    `EvalPassthrough` (the harness sends text and reads the LLM's text);
  * the post-call steps (call classification, summary LLM, host e-mail, guest
    memory) are skipped: they cost money and message people, and an eval call is
    neither a lead nor a guest;
  * the database must be an eval database, never one that holds a real host's data.

Nothing in here runs on a production call: `_run_pipeline(eval_mode=False)` is the
default and every branch that reads `eval_mode` sits behind it.
"""

import os
from urllib.parse import urlsplit

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.config import settings
from app.utils.webhook_auth import verify_shared_secret_token

# A database name must contain one of these for the eval bot to start against it.
_EVAL_DATABASE_MARKERS = ("eval", "test")


class EvalPassthrough(FrameProcessor):
    """Stands in for the STT or TTS service: forwards every frame untouched. The
    pipeline attaches an `on_error` handler to both services (health counting); every
    FrameProcessor already has that event, and here it simply never fires."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


def database_name(database_url: str) -> str:
    """The database name in a SQLAlchemy URL ('' when there is none)."""
    return urlsplit(database_url).path.rpartition("/")[2]


def assert_safe_eval_database(database_url: str) -> None:
    """Refuse to seed or run against a database that could hold a real host's data.

    `EVAL_ALLOW_ANY_DATABASE=1` is the escape hatch for an eval database that has an
    unusual name; it is never set by anything in this repo."""
    name = database_name(database_url)
    if os.environ.get("EVAL_ALLOW_ANY_DATABASE") == "1":
        return
    if not any(marker in name.lower() for marker in _EVAL_DATABASE_MARKERS):
        raise RuntimeError(
            f"Refusing to run the eval bot against database {name!r}: its name must contain "
            f"{' or '.join(repr(m) for m in _EVAL_DATABASE_MARKERS)}. Point DATABASE_URL at a "
            "dedicated eval database (see docs/documentation/golden-eval-suite.md)."
        )


MIN_EVAL_ROUTE_TOKEN_LENGTH = 24


def eval_route_authorized(token: str | None) -> bool:
    """May this websocket drive the eval bot? Only when ALL hold: the route is switched on (a long enough
    EVAL_ROUTE_TOKEN is configured), the URL carries exactly that token, and the database is an eval one (or the
    operator has said otherwise with EVAL_ALLOW_ANY_DATABASE=1). Anything else is refused, the same as a missing
    route: a real caller can never reach it, and a production deployment refuses it on the database check alone."""
    configured = settings.eval_route_token
    if len(configured) < MIN_EVAL_ROUTE_TOKEN_LENGTH:
        return False
    if not verify_shared_secret_token(token, configured):
        return False
    try:
        assert_safe_eval_database(settings.database_url)
    except RuntimeError:
        return False
    return True
