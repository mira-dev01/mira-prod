import asyncio

from pipecat.runner.types import CallData

from app.voice import pipeline


class _FakeWebSocket:
    """Records send_text calls and never actually closes -- close() just
    flips a flag, mirroring the real websocket.close() call site closely
    enough for this test without needing a live connection."""

    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True


async def test_no_matching_property_or_lead_line_cancels_the_ringing_task(db_session):
    # run_voice_pipeline's early-return branch (no Property/lead exophone
    # configured for the dialed number) must still stop the ringing task it
    # started at the top -- otherwise a misdialed/unconfigured number would
    # leave a ring tone looping forever against a websocket nothing else is
    # managing. No fixtures seed a property/lead for this made-up number, so
    # the real DB (per this repo's no-DB-mocking policy) genuinely has no
    # match and the early-return branch is what actually runs.
    ws = _FakeWebSocket()
    call_data = CallData(
        stream_id="stream-unmatched",
        call_id="call-unmatched",
        **{"from": "+919999999999", "to": "+910000000000"},
    )

    await pipeline.run_voice_pipeline(ws, call_data)

    assert ws.closed is True
    # The task was created inside run_voice_pipeline itself and is not
    # returned -- so the only externally observable proof it was actually
    # stopped (not left orphaned) is that the function returned at all
    # without hanging, plus that no frames kept arriving after return.
    sent_at_return = len(ws.sent)
    await asyncio.sleep(0.1)
    assert len(ws.sent) == sent_at_return
