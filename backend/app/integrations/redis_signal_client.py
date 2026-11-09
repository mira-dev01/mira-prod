"""Redis pub/sub for cross-process signals ("a host just claimed Take Call
for call X" -> the process actually running call X).

This is the THIRD Redis use in the codebase and deliberately its own module:

- redis_client.py:        a CACHE; failures are silent no-ops.
- redis_lease_client.py:  correctness-bearing call ownership; failures are
                          surfaced to CallCoordinator.
- redis_signal_client.py (here): a best-effort NOTIFICATION. The durable fact
                          (the claim) is already in Postgres before anything
                          is published; this only tells a live pipeline to act
                          on it NOW. So: publish() never raises (False = "no
                          one heard it", which the caller logs), and the
                          listener reconnects forever with backoff.

Reuses redis_client.get_client() -- one connection pool per process.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.integrations import redis_client
from app.observability import health

logger = logging.getLogger(__name__)

# How long a blocking read waits before the loop checks again. Deliberately
# explicit: the shared client's socket_timeout (3s) would otherwise raise on
# every quiet 3 seconds of a perfectly healthy subscription.
_POLL_SECONDS = 1.0
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0


async def publish(channel: str, message: str) -> int:
    """Publishes `message`; returns how many subscribers received it (0 when
    nobody is listening, Redis is unconfigured, or the publish failed).
    Never raises."""
    client = redis_client.get_client()
    if client is None:
        return 0
    try:
        return int(await client.publish(channel, message))
    except Exception as exc:
        health.record("redis", "error", error=exc, kind="signal_publish_failed", op="PUBLISH")
        logger.warning("Redis PUBLISH to %s failed -- the signal was not delivered", channel)
        return 0


async def listen(channel: str, handler: Callable[[str], Awaitable[None] | None]) -> None:
    """Subscribes to `channel` and calls `handler(message)` for each message,
    until cancelled. Reconnects with capped exponential backoff on any
    connection problem. Returns immediately (logging once) if Redis is not
    configured -- then there is simply no cross-process signalling, and
    callers fall back to the in-process path.

    A handler that raises is logged and the loop continues: one bad message
    must not kill the subscription."""
    if redis_client.get_client() is None:
        logger.info("redis_signal_listener_disabled channel=%s -- REDIS_URL is not set", channel)
        return

    backoff = _INITIAL_BACKOFF_SECONDS
    while True:
        pubsub = None
        try:
            client = redis_client.get_client()
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel)
            logger.info("redis_signal_listener_subscribed channel=%s", channel)
            backoff = _INITIAL_BACKOFF_SECONDS
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_POLL_SECONDS)
                if message is None:
                    continue
                try:
                    result = handler(message["data"])
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("redis_signal_handler_failed channel=%s", channel)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            health.record("redis", "error", error=exc, kind="signal_listener_failed", op="SUBSCRIBE")
            logger.warning(
                "redis_signal_listener_lost channel=%s error=%s -- reconnecting in %.0fs", channel, exc, backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.debug("Ignoring error while closing a pubsub connection", exc_info=True)
