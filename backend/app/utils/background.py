"""One place to start fire-and-forget work safely.

`asyncio.create_task(coro())` with the result discarded is a latent bug: the
event loop keeps only a WEAK reference to a task, so with nothing else holding
it the task "can be garbage collected at any time, even before it's done"
(asyncio's own documentation). For this app that means silently losing the
guest-memory update, the call-summary email, an escalation email, a WhatsApp
send, or the busy-recovery message.

`spawn()` keeps a strong reference until the task finishes, and logs any
exception it raised (otherwise it surfaces only as an easy-to-miss "Task
exception was never retrieved" at garbage-collection time, with no context).
Use it for every detached task whose handle you do not keep yourself. Tasks
you DO keep and later cancel/await (a lease-renewal loop, a ringing tone)
already hold their own reference and do not need it.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Strong references to in-flight detached tasks. Each task removes itself via
# a done-callback, so the steady-state size is "how many are running right
# now", not an ever-growing leak.
tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """asyncio.create_task() that survives garbage collection and logs failures."""
    task = asyncio.create_task(coro, name=name)
    tasks.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task: asyncio.Task) -> None:
    tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()  # also marks it retrieved
    if error is not None:
        logger.error("background_task_failed name=%s", task.get_name(), exc_info=error)
