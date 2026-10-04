"""Metered usage of paid external services, for the internal admin panel's
usage/cost/balance views (app/services/admin_monitor_service.py).

Strictly fail-open, like every optional integration: a metering write can
never raise into a caller, and the detached variant never adds latency to
one. Nothing here is ever read back into a live call.

Two write paths:
- record_call_usage: in-call usage aggregated per call by
  app/voice/call_metrics.py, written once from on_pipeline_finished.
- record_usage_detached: one out-of-call request (post-call LLM, WhatsApp
  send, email, SearchApi, Bright Data, embeddings), fire-and-forget with its
  own DB session so the caller's own transaction is never touched.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.service_usage_event import ServiceUsageEvent

logger = logging.getLogger(__name__)

# Strong references so detached tasks aren't garbage-collected mid-flight
# (asyncio only keeps weak references to tasks).
_background_tasks: set[asyncio.Task] = set()

# (call_session_id, host user_id) of the live call this code is running
# inside, if any. Bound once at the top of the voice pipeline
# (app/voice/pipeline.py::_run_pipeline_inner); asyncio copies context into
# every task created afterwards, so a WhatsApp send / SearchApi lookup made
# by a tool mid-call, or the post-call summary/classification LLM request,
# is attributed to the right call and host without threading ids through
# every integration's signature. Per-task, so concurrent calls never mix.
_usage_context: ContextVar[tuple[uuid.UUID | None, uuid.UUID | None]] = ContextVar(
    "usage_context", default=(None, None)
)


def bind_usage_context(call_session_id: uuid.UUID | None, user_id: uuid.UUID | None) -> None:
    _usage_context.set((call_session_id, user_id))


async def record_call_usage(
    db: AsyncSession,
    call_session_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    rows: Iterable,
) -> None:
    """Persists a call's aggregated usage rows (app.voice.call_metrics.UsageRow).
    Never raises."""
    rows = [row for row in rows if row.quantity and row.quantity > 0]
    if not settings.usage_metering_enabled or not rows:
        return
    try:
        db.add_all(
            [
                ServiceUsageEvent(
                    service=row.service,
                    unit=row.unit,
                    quantity=float(row.quantity),
                    model=row.model,
                    call_session_id=call_session_id,
                    user_id=user_id,
                )
                for row in rows
            ]
        )
        await db.commit()
    except Exception:
        logger.exception("Failed to record call usage for call_session_id=%s", call_session_id)
        await db.rollback()


async def record_usage(
    service: str,
    unit: str,
    quantity: float,
    *,
    model: str | None = None,
    call_session_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    """One usage row in its own session. Never raises."""
    if not settings.usage_metering_enabled or not quantity or quantity <= 0:
        return
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            db.add(
                ServiceUsageEvent(
                    service=service,
                    unit=unit,
                    quantity=float(quantity),
                    model=model,
                    call_session_id=call_session_id,
                    user_id=user_id,
                    metadata_json=metadata or {},
                )
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to record usage service=%s unit=%s", service, unit)


def record_usage_detached(service: str, unit: str, quantity: float, **kwargs) -> None:
    """Fire-and-forget record_usage -- safe to call from any async code path
    without awaiting; a no-op outside a running event loop."""
    if not settings.usage_metering_enabled or not quantity or quantity <= 0:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    bound_call, bound_user = _usage_context.get()
    if kwargs.get("call_session_id") is None and bound_call is not None:
        kwargs["call_session_id"] = bound_call
    if kwargs.get("user_id") is None and bound_user is not None:
        kwargs["user_id"] = bound_user
    task = loop.create_task(record_usage(service, unit, quantity, **kwargs))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def record_llm_response_usage(service: str, response, *, model: str | None = None, purpose: str) -> None:
    """Meters an OpenAI/Groq-style (`usage.prompt_tokens`/`completion_tokens`)
    or Anthropic-style (`usage.input_tokens`/`output_tokens`) SDK response
    from an out-of-call LLM request. Never raises."""
    try:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            return

        def _get(name):
            return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

        prompt = _get("prompt_tokens") or _get("input_tokens") or 0
        completion = _get("completion_tokens") or _get("output_tokens") or 0
        resolved_model = model or getattr(response, "model", None)
        if resolved_model is None and isinstance(response, dict):
            resolved_model = response.get("model")
        meta = {"purpose": purpose}
        record_usage_detached(service, "prompt_tokens", prompt, model=resolved_model, metadata=meta)
        record_usage_detached(service, "completion_tokens", completion, model=resolved_model, metadata=meta)
    except Exception:
        logger.exception("Failed to meter LLM response usage service=%s", service)
