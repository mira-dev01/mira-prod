"""Covers the Phase 1A hook in _ReconnectingSarvamSTTService._handle_message:
Sarvam server-side VAD events must be counted into AudioInputContext AND
still reach the parent handler unchanged (observation only) -- and a broken
telemetry path must never stop the parent from handling the message."""

from types import SimpleNamespace

import pytest
from pipecat.services.sarvam.stt import SarvamSTTService

from app.voice.audio_input_observer import AudioInputContext
from app.voice.pipeline import _ReconnectingSarvamSTTService


def _event(signal: str):
    return SimpleNamespace(type="events", data=SimpleNamespace(signal_type=signal, occured_at=0))


@pytest.fixture
def parent_calls(monkeypatch):
    calls = []

    async def _fake_parent(self, message):
        calls.append(message)

    monkeypatch.setattr(SarvamSTTService, "_handle_message", _fake_parent)
    return calls


@pytest.mark.asyncio
async def test_vad_events_are_counted_and_still_forwarded(parent_calls):
    context = AudioInputContext()
    stt = _ReconnectingSarvamSTTService(api_key="test", audio_input_context=context)

    await stt._handle_message(_event("START_SPEECH"))
    await stt._handle_message(_event("END_SPEECH"))
    await stt._handle_message(SimpleNamespace(type="data", data=SimpleNamespace(transcript="hi")))

    assert context.sarvam_vad_events == {"START_SPEECH": 1, "END_SPEECH": 1}
    assert context.last_sarvam_start_speech_at is not None
    assert len(parent_calls) == 3


@pytest.mark.asyncio
async def test_without_context_messages_pass_straight_through(parent_calls):
    stt = _ReconnectingSarvamSTTService(api_key="test")

    await stt._handle_message(_event("START_SPEECH"))

    assert len(parent_calls) == 1


@pytest.mark.asyncio
async def test_telemetry_failure_never_blocks_the_parent_handler(parent_calls):
    class _Broken(AudioInputContext):
        def note_sarvam_vad_event(self, signal):
            raise RuntimeError("boom")

    stt = _ReconnectingSarvamSTTService(api_key="test", audio_input_context=_Broken())

    await stt._handle_message(_event("START_SPEECH"))

    assert len(parent_calls) == 1
