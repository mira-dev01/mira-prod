"""Take Call must reach a live call even when the call runs in a DIFFERENT
process than the HTTP request that claims it (a second replica, or voice split
from the API). Real Redis, same convention as test_call_coordinator.py; the
"other process" is simulated by publishing straight to the channel."""

import asyncio
import logging
import uuid

import pytest

from app.integrations import redis_client, redis_signal_client
from app.voice import handoff_signal


@pytest.fixture(autouse=True)
def _clean_registry():
    handoff_signal._handoff_events.clear()
    yield
    handoff_signal._handoff_events.clear()


async def _wait_until_subscribed(timeout: float = 3.0) -> None:
    client = redis_client.get_client()
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        counts = await client.pubsub_numsub(handoff_signal.HANDOFF_CHANNEL)
        if counts and counts[0][1] >= 1:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("listener never subscribed")


@pytest.fixture
async def listener():
    task = asyncio.create_task(handoff_signal.run_listener())
    await _wait_until_subscribed()
    yield task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_request_from_another_process_sets_the_local_event(listener):
    call_id = uuid.uuid4()
    handoff_signal.register_call(call_id)

    receivers = await redis_signal_client.publish(handoff_signal.HANDOFF_CHANNEL, str(call_id))

    assert receivers >= 1
    await asyncio.wait_for(handoff_signal.wait_for_handoff_request(call_id), timeout=2)


async def test_request_for_a_call_on_another_process_is_ignored_here(listener):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    handoff_signal.register_call(mine)

    await redis_signal_client.publish(handoff_signal.HANDOFF_CHANNEL, str(theirs))
    await asyncio.sleep(0.3)

    assert not handoff_signal._handoff_events[mine].is_set()


async def test_a_malformed_message_does_not_kill_the_listener(listener, caplog):
    call_id = uuid.uuid4()
    handoff_signal.register_call(call_id)

    with caplog.at_level(logging.WARNING):
        await redis_signal_client.publish(handoff_signal.HANDOFF_CHANNEL, "not-a-uuid")
        await asyncio.sleep(0.2)
    await redis_signal_client.publish(handoff_signal.HANDOFF_CHANNEL, str(call_id))

    await asyncio.wait_for(handoff_signal.wait_for_handoff_request(call_id), timeout=2)
    assert any("malformed" in r.getMessage() for r in caplog.records)


async def test_same_process_request_needs_no_redis_round_trip(monkeypatch):
    call_id = uuid.uuid4()
    handoff_signal.register_call(call_id)

    async def _must_not_publish(*args, **kwargs):
        raise AssertionError("published although the call is local")

    monkeypatch.setattr(redis_signal_client, "publish", _must_not_publish)

    assert await handoff_signal.request_handoff_anywhere(call_id) is True
    assert handoff_signal._handoff_events[call_id].is_set()


async def test_request_for_a_remote_call_is_published_and_acknowledged(listener):
    # Not registered here => must go over Redis; the running listener counts as a receiver.
    assert await handoff_signal.request_handoff_anywhere(uuid.uuid4()) is True


async def test_undeliverable_request_is_reported_not_silent(caplog):
    # No listener anywhere, call not registered locally.
    with caplog.at_level(logging.WARNING):
        delivered = await handoff_signal.request_handoff_anywhere(uuid.uuid4())
    assert delivered is False
    assert any("handoff_signal_not_delivered" in r.getMessage() for r in caplog.records)


async def test_listener_is_a_noop_when_redis_is_not_configured(monkeypatch):
    monkeypatch.setattr(redis_client, "_client", None)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    # Returns immediately instead of spinning or raising.
    await asyncio.wait_for(handoff_signal.run_listener(), timeout=1)
    assert await redis_signal_client.publish("any", "x") == 0


async def test_listener_reconnects_after_losing_the_connection(monkeypatch):
    monkeypatch.setattr(redis_signal_client, "_INITIAL_BACKOFF_SECONDS", 0.05)
    received: list[str] = []
    attempts = {"n": 0}

    class _FakePubSub:
        def __init__(self, fail: bool):
            self._fail = fail
            self._sent = False

        async def subscribe(self, channel):
            if self._fail:
                raise ConnectionError("connection reset")

        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            if not self._sent:
                self._sent = True
                return {"type": "message", "data": "after-reconnect"}
            await asyncio.sleep(timeout or 0.05)
            return None

        async def aclose(self):
            pass

    class _FakeClient:
        def pubsub(self, **kwargs):
            attempts["n"] += 1
            return _FakePubSub(fail=attempts["n"] == 1)

    monkeypatch.setattr(redis_client, "get_client", lambda: _FakeClient())

    task = asyncio.create_task(redis_signal_client.listen("c", received.append))
    for _ in range(100):
        if received:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert received == ["after-reconnect"]
    assert attempts["n"] >= 2
