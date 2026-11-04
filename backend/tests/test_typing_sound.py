from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.typing_sound import TYPING_TOOLS, TypingSoundProcessor, _CHUNK_BYTES, _TYPING_PCM


def _started(name: str = "search_faq", call_id: str = "c1") -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name=name, tool_call_id=call_id, arguments={}, context=None)]
    )


def _result(call_id: str = "c1") -> FunctionCallResultFrame:
    return FunctionCallResultFrame(function_name="search_faq", tool_call_id=call_id, arguments={}, result="ok")


def _typing(frames) -> list[OutputAudioRawFrame]:
    return [f for f in frames if isinstance(f, OutputAudioRawFrame)]


def _proc(**kw) -> TypingSoundProcessor:
    return TypingSoundProcessor(start_delay=0.05, resume_delay=0.05, **kw)


def test_asset_loaded_and_covers_lookup_tools():
    assert _TYPING_PCM and len(_TYPING_PCM) > 8000 * 2 * 3  # at least a few seconds
    assert {"search_faq", "check_calendar", "recommend_properties", "get_pricing", "negotiate_rate"} <= TYPING_TOOLS
    assert "update_lead" not in TYPING_TOOLS and "end_call" not in TYPING_TOOLS


async def test_quick_lookup_makes_no_sound():
    down, _ = await run_test(
        TypingSoundProcessor(start_delay=0.3),
        frames_to_send=[_started(), SleepFrame(sleep=0.05), _result(), SleepFrame(sleep=0.4)],
    )
    assert _typing(down) == []


async def test_slow_lookup_plays_plain_output_audio_in_small_chunks():
    down, _ = await run_test(_proc(), frames_to_send=[_started(), SleepFrame(sleep=0.4)])
    chunks = _typing(down)
    assert len(chunks) >= 3
    for chunk in chunks:
        # Plain OutputAudioRawFrame: a TTS subclass would flip the transport's
        # bot-speaking state and confuse the silence watchdog.
        assert not isinstance(chunk, TTSAudioRawFrame)
        assert chunk.sample_rate == 8000 and chunk.num_channels == 1
        assert len(chunk.audio) == _CHUNK_BYTES


async def test_stops_the_moment_the_result_arrives():
    down, _ = await run_test(
        _proc(), frames_to_send=[_started(), SleepFrame(sleep=0.3), _result(), SleepFrame(sleep=0.3)]
    )
    result_index = next(i for i, f in enumerate(down) if isinstance(f, FunctionCallResultFrame))
    assert _typing(down[:result_index])  # it did play before
    assert _typing(down[result_index + 1 :]) == []


async def test_untracked_tools_are_silent():
    down, _ = await run_test(_proc(), frames_to_send=[_started("update_lead"), SleepFrame(sleep=0.3)])
    assert _typing(down) == []


async def test_never_starts_while_guest_is_speaking_and_resumes_after():
    down, _ = await run_test(
        _proc(),
        frames_to_send=[UserStartedSpeakingFrame(), _started(), SleepFrame(sleep=0.3)],
    )
    assert _typing(down) == []

    down, _ = await run_test(
        _proc(),
        frames_to_send=[
            UserStartedSpeakingFrame(),
            _started(),
            SleepFrame(sleep=0.2),
            UserStoppedSpeakingFrame(),
            SleepFrame(sleep=0.3),
        ],
    )
    assert _typing(down)


async def test_guest_cutting_in_stops_typing():
    down, _ = await run_test(
        _proc(),
        frames_to_send=[_started(), SleepFrame(sleep=0.3), UserStartedSpeakingFrame(), SleepFrame(sleep=0.3)],
    )
    cut = next(i for i, f in enumerate(down) if isinstance(f, UserStartedSpeakingFrame))
    assert _typing(down[:cut])
    assert _typing(down[cut + 1 :]) == []


async def test_yields_to_mira_speaking_then_resumes_when_she_stops():
    down, _ = await run_test(
        _proc(),
        frames_to_send=[_started(), SleepFrame(sleep=0.3), TTSStartedFrame(), SleepFrame(sleep=0.3)],
    )
    spoke = next(i for i, f in enumerate(down) if isinstance(f, TTSStartedFrame))
    assert _typing(down[:spoke])
    assert _typing(down[spoke + 1 :]) == []

    down, _ = await run_test(
        _proc(),
        frames_to_send=[
            _started(),
            SleepFrame(sleep=0.2),
            TTSStartedFrame(),
            SleepFrame(sleep=0.1),
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.3),
        ],
    )
    stopped = next(i for i, f in enumerate(down) if isinstance(f, BotStoppedSpeakingFrame))
    assert _typing(down[stopped + 1 :])  # lookup still running -> typing resumes


async def test_does_not_start_if_mira_is_already_speaking_when_lookup_begins():
    down, _ = await run_test(
        _proc(), frames_to_send=[TTSStartedFrame(), _started(), SleepFrame(sleep=0.3)]
    )
    assert _typing(down) == []


async def test_missing_asset_is_a_pass_through():
    proc = TypingSoundProcessor(start_delay=0.01, pcm=None)
    down, _ = await run_test(proc, frames_to_send=[_started(), SleepFrame(sleep=0.2), _result()])
    assert _typing(down) == []
    assert any(isinstance(f, FunctionCallResultFrame) for f in down)
