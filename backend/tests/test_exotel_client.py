import asyncio

import respx
from httpx import Response

from app.config import settings
from app.integrations import exotel_client


async def test_hangup_call_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "exotel_sid", None)
    result = await exotel_client.hangup_call("some-call-sid")
    assert result == {"status": "skipped", "reason": "Exotel credentials not configured"}


@respx.mock
async def test_hangup_call_posts_status_completed(monkeypatch):
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    route = respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-123.json").mock(
        return_value=Response(200, json={"Call": {"Sid": "call-sid-123", "Status": "completed"}})
    )

    result = await exotel_client.hangup_call("call-sid-123")

    assert route.called
    sent_body = route.calls.last.request.content.decode()
    assert "Status=completed" in sent_body
    assert result == {"Call": {"Sid": "call-sid-123", "Status": "completed"}}


@respx.mock
async def test_hangup_call_as_detached_task_does_not_block_the_caller(monkeypatch):
    # Regression for the normal-call-termination fix in app/voice/pipeline.py's
    # on_pipeline_finished: that handler now fires hangup_call via
    # asyncio.create_task instead of awaiting it inline, specifically so a
    # slow Exotel API response can never gate the pipeline's own teardown.
    # This test proves the actual mechanism the fix relies on -- a slow
    # hangup_call response -- against the real exotel_client.hangup_call
    # coroutine, the same one on_pipeline_finished wraps.
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    release_response = asyncio.Event()

    async def _slow_hangup(request):
        await release_response.wait()
        return Response(200, json={"Call": {"Sid": "call-sid-slow", "Status": "completed"}})

    respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-slow.json").mock(
        side_effect=_slow_hangup
    )

    hangup_completed = asyncio.Event()

    async def _detached_hangup():
        await exotel_client.hangup_call("call-sid-slow")
        hangup_completed.set()

    # Same shape as on_pipeline_finished's own detached _hangup() closure:
    # fire-and-forget, caller does not await it.
    task = asyncio.create_task(_detached_hangup())
    await asyncio.sleep(0.05)  # give the task a chance to start and block on the slow response

    # The caller (standing in for on_pipeline_finished) is free to proceed
    # immediately -- the whole point of the fix -- even though Exotel's
    # response hasn't arrived yet.
    assert not hangup_completed.is_set()
    assert not task.done()

    release_response.set()
    await task

    assert hangup_completed.is_set()


@respx.mock
async def test_hangup_call_is_idempotent_per_call_sid(monkeypatch):
    # Regression for duplicate-hangup protection: on_pipeline_finished and
    # _reject_call_as_busy are mutually exclusive per call (a call takes
    # exactly one of the two paths through run_voice_pipeline), but a single
    # call CAN legitimately reach the same hangup call site twice within one
    # process -- e.g. a watchdog/cancellation teardown racing the normal
    # EndFrame-driven teardown for the same call_sid. Exotel itself tolerates
    # a redundant "Status: completed" POST against an already-ended call, so
    # this isn't correctness-critical, but it should still not fire two real
    # HTTP requests for what is logically one hangup.
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    route = respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-dup.json").mock(
        return_value=Response(200, json={"Call": {"Sid": "call-sid-dup", "Status": "completed"}})
    )

    first = await exotel_client.hangup_call("call-sid-dup")
    second = await exotel_client.hangup_call("call-sid-dup")

    assert route.call_count == 1
    assert first == {"Call": {"Sid": "call-sid-dup", "Status": "completed"}}
    assert second == {"status": "skipped", "reason": "hangup already requested for this call"}


@respx.mock
async def test_hangup_call_idempotency_is_scoped_per_call_sid(monkeypatch):
    # A different call_sid must never be suppressed by another call's
    # already-requested hangup -- the guard is per-call, not global.
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-a.json").mock(
        return_value=Response(200, json={"Call": {"Sid": "call-sid-a", "Status": "completed"}})
    )
    route_b = respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-b.json").mock(
        return_value=Response(200, json={"Call": {"Sid": "call-sid-b", "Status": "completed"}})
    )

    await exotel_client.hangup_call("call-sid-a")
    result_b = await exotel_client.hangup_call("call-sid-b")

    assert route_b.called
    assert result_b == {"Call": {"Sid": "call-sid-b", "Status": "completed"}}


@respx.mock
async def test_hangup_call_failure_does_not_permanently_block_a_retry(monkeypatch):
    # Regression: the idempotency guard marks a call_sid as "requested" only
    # AFTER a real success (response.raise_for_status() passing), never up
    # front -- marking up front would let one transient Exotel failure (a
    # network error, a 5xx) silently and permanently prevent this process
    # from ever attempting that call_sid's hangup again, which is worse than
    # the duplicate-request problem the guard exists to solve.
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    route = respx.post("https://api.exotel.com/v1/Accounts/test-sid/Calls/call-sid-retry.json")
    route.side_effect = [
        Response(500, json={"error": "internal error"}),
        Response(200, json={"Call": {"Sid": "call-sid-retry", "Status": "completed"}}),
    ]

    try:
        await exotel_client.hangup_call("call-sid-retry")
    except Exception:
        pass
    else:
        raise AssertionError("expected the first (500) attempt to raise")

    # A second attempt for the same call_sid must still go out for real --
    # not silently skipped as "already requested".
    result = await exotel_client.hangup_call("call-sid-retry")

    assert route.call_count == 2
    assert result == {"Call": {"Sid": "call-sid-retry", "Status": "completed"}}

    # Now that it has actually succeeded, a THIRD attempt is correctly
    # suppressed by the idempotency guard.
    third = await exotel_client.hangup_call("call-sid-retry")
    assert route.call_count == 2
    assert third == {"status": "skipped", "reason": "hangup already requested for this call"}
