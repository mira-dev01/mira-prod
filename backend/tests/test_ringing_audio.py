import asyncio
import json

from app.voice.ringing_audio import _CHUNK_BYTES, _RINGING_TONE_PCM, play_ringing_tone


class _FakeWebSocket:
    """Records every send_text call instead of touching a real socket, so
    tests can assert exactly how many frames went out and that none arrive
    after cancellation -- the property that actually matters for "the ring
    tone must stop before Mira speaks", not just that cancel() was called."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str):
        self.sent.append(data)


async def test_loops_past_a_single_cycle_until_cancelled():
    # The clip is one ring cycle; a real call's setup can take longer than
    # that, so playback must keep looping rather than stopping once the
    # clip's own bytes run out.
    ws = _FakeWebSocket()
    task = asyncio.create_task(play_ringing_tone(ws, "stream123"))

    frames_in_one_cycle = len(range(0, len(_RINGING_TONE_PCM), _CHUNK_BYTES))
    # Let enough real time pass for more than one full cycle -- each frame
    # sleeps 20ms, so this comfortably covers 2x the clip length.
    await asyncio.sleep(0.02 * frames_in_one_cycle * 2.2)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(ws.sent) > frames_in_one_cycle  # proves it looped, not just played once


async def test_frames_are_well_formed_exotel_media_events():
    ws = _FakeWebSocket()
    task = asyncio.create_task(play_ringing_tone(ws, "stream-abc"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ws.sent  # got at least one frame before cancellation
    first = json.loads(ws.sent[0])
    assert first["event"] == "media"
    assert first["streamSid"] == "stream-abc"
    assert "payload" in first["media"]


async def test_cancel_and_await_stops_sends_immediately_no_frames_arrive_after():
    # This is the airtight-stop guarantee itself: once cancel() has been
    # called and the task awaited to completion, no further send_text calls
    # can occur -- there is no window where a ring frame could still land on
    # the socket after the caller believes playback has stopped.
    ws = _FakeWebSocket()
    task = asyncio.create_task(play_ringing_tone(ws, "streamXYZ"))
    await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    count_at_cancel = len(ws.sent)
    # Give the event loop several more real turns -- if the loop somehow kept
    # running (e.g. cancellation didn't actually propagate into the sleep),
    # more frames would show up here.
    await asyncio.sleep(0.2)

    assert len(ws.sent) == count_at_cancel
    assert task.done()
    assert task.cancelled()


async def test_send_failure_is_swallowed_not_raised(monkeypatch):
    # A broken/closing socket mid-ring must never take the real call down --
    # same contract as the removed holding-message feature.
    class _FailingWebSocket:
        async def send_text(self, data: str):
            raise RuntimeError("socket already closing")

    # Should return (not raise) once the underlying error propagates out of
    # the loop via the broad except clause.
    await play_ringing_tone(_FailingWebSocket(), "stream-fail")
