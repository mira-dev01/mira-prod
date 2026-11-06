import asyncio

import pytest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

from app.voice.backchannel_start_strategy import BackchannelAwareUserTurnStartStrategy, is_backchannel


@pytest.mark.parametrize(
    "text",
    ["hmm", "Okay.", "haan", "achha ji", "हाँ", "ठीक है", "ok ok", "I see", "yeah right"],
)
def test_pure_acknowledgements_are_backchannel(text):
    assert is_backchannel(text) is True


@pytest.mark.parametrize(
    "text",
    ["", "no", "wait", "ruko", "we are ten people", "okay but what about the price", "haan par kitna lagega", "9326359081"],
)
def test_substantive_speech_is_not_backchannel(text):
    assert is_backchannel(text) is False


async def _strategy(min_interrupt_secs=0.05):
    strategy = BackchannelAwareUserTurnStartStrategy(min_interrupt_secs=min_interrupt_secs)
    tm = TaskManager()
    tm.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
    await strategy.setup(tm)
    events = {"started": 0, "reset": 0}

    @strategy.event_handler("on_user_turn_started")
    async def _started(strategy, params):
        events["started"] += 1

    @strategy.event_handler("on_reset_aggregation")
    async def _reset(strategy):
        events["reset"] += 1

    return strategy, events


def _tx(text):
    return TranscriptionFrame(text=text, user_id="u", timestamp="0")


@pytest.mark.asyncio
async def test_vad_opens_turn_immediately_when_bot_is_silent():
    strategy, events = await _strategy()
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0))
    assert events["started"] == 1


@pytest.mark.asyncio
async def test_short_burst_while_bot_speaks_does_not_interrupt():
    strategy, events = await _strategy(min_interrupt_secs=0.2)
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0))
    await asyncio.sleep(0.05)
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=0))
    await asyncio.sleep(0.3)
    assert events["started"] == 0


@pytest.mark.asyncio
async def test_sustained_speech_while_bot_speaks_interrupts():
    strategy, events = await _strategy(min_interrupt_secs=0.05)
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0))
    await asyncio.sleep(0.15)
    assert events["started"] == 1


@pytest.mark.asyncio
async def test_backchannel_transcript_during_bot_speech_is_dropped_not_a_turn():
    strategy, events = await _strategy()
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(_tx("hmm"))
    assert events["started"] == 0
    assert events["reset"] == 1


@pytest.mark.asyncio
async def test_backchannel_transcript_arriving_just_after_bot_stops_is_still_dropped():
    strategy, events = await _strategy(min_interrupt_secs=0.2)
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0))
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=0))
    await strategy.process_frame(BotStoppedSpeakingFrame())
    await strategy.process_frame(_tx("achha"))
    assert events["started"] == 0
    assert events["reset"] == 1


@pytest.mark.asyncio
async def test_substantive_transcript_during_bot_speech_interrupts():
    strategy, events = await _strategy()
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(_tx("wait we are ten people"))
    assert events["started"] == 1
    assert events["reset"] == 0


@pytest.mark.asyncio
async def test_backchannel_word_when_bot_is_silent_and_no_prior_burst_is_a_real_answer():
    strategy, events = await _strategy()
    await strategy.process_frame(_tx("yes"))
    assert events["started"] == 1


@pytest.mark.asyncio
async def test_partial_with_real_words_interrupts_before_timer():
    from pipecat.frames.frames import InterimTranscriptionFrame

    strategy, events = await _strategy(min_interrupt_secs=5.0)
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0))
    await strategy.process_frame(InterimTranscriptionFrame(text="wait we are", user_id="u", timestamp="0"))
    assert events["started"] == 1


@pytest.mark.asyncio
async def test_partial_that_is_only_an_acknowledgement_does_not_interrupt():
    from pipecat.frames.frames import InterimTranscriptionFrame

    strategy, events = await _strategy(min_interrupt_secs=5.0)
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(InterimTranscriptionFrame(text="hmm", user_id="u", timestamp="0"))
    assert events["started"] == 0
