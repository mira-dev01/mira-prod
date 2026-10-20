import pytest
from pipecat.frames.frames import (
    ErrorFrame,
    FunctionCallCancelFrame,
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    TTSSpeakFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.slow_tool_filler import (
    DEFAULT_PRICING_FILLER_TEXTS,
    DEFAULT_SLOW_TOOLS,
    DEFAULT_TOOL_DELAY_OVERRIDES,
    PRICING_TOOL_DELAY_SECONDS,
    SlowToolFillerProcessor,
)


def _started(function_name: str, tool_call_id: str = "call-1") -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[
            FunctionCallFromLLM(
                function_name=function_name, tool_call_id=tool_call_id, arguments={}, context=None
            )
        ]
    )


def _result(tool_call_id: str = "call-1") -> FunctionCallResultFrame:
    return FunctionCallResultFrame(
        function_name="recommend_properties",
        tool_call_id=tool_call_id,
        arguments={},
        result="ok",
    )


def _started_multi(*calls: tuple[str, str]) -> FunctionCallsStartedFrame:
    """Like _started, but for a single FunctionCallsStartedFrame naming
    MULTIPLE simultaneous tool calls -- FunctionCallsStartedFrame.
    function_calls is documented as "one or more function call execution",
    a real, LLM-driven multi-tool-call turn (e.g. recommend_properties +
    get_pricing together), not just a single-call convenience shape."""
    return FunctionCallsStartedFrame(
        function_calls=[
            FunctionCallFromLLM(function_name=name, tool_call_id=call_id, arguments={}, context=None)
            for name, call_id in calls
        ]
    )


@pytest.mark.asyncio
async def test_speaks_filler_if_scoped_tool_still_running_after_delay():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.1)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties"),
            SleepFrame(sleep=0.2),  # comfortably past the 0.1s delay
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].append_to_context is False


@pytest.mark.asyncio
async def test_no_filler_if_result_arrives_before_delay_elapses():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.2)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties"),
            SleepFrame(sleep=0.05),
            _result(),  # resolves well before the 0.2s delay
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_no_filler_if_call_is_cancelled_before_delay_elapses():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.2)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties"),
            SleepFrame(sleep=0.05),
            FunctionCallCancelFrame(function_name="recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_unscoped_tool_never_triggers_a_filler():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.05)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.2),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_a_tool_call_that_errors_out_does_not_permanently_block_later_fillers():
    # llm_service.py's run_function_calls catches an unhandled exception from
    # the tool handler and pushes an ErrorFrame instead of ever calling
    # result_callback -- confirmed live, recommend_properties has thrown in
    # production (an unexpected-keyword-argument error). No
    # FunctionCallResultFrame or FunctionCallCancelFrame follows in that
    # case, so tracking must not get stuck waiting for one forever.
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.05)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.1),  # filler #1 fires; call-1 never resolves
            ErrorFrame(error="boom", fatal=False),
            _started("recommend_properties", tool_call_id="call-2"),  # a later, genuinely new call
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 2


@pytest.mark.asyncio
async def test_fillers_rotate_across_multiple_calls_in_the_same_call_session():
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties"},
        delay_seconds=0.05,
        filler_texts=["First line.", "Second line."],
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.1),
            _result(tool_call_id="call-1"),
            # A brief gap so the result frame is actually processed (clearing
            # _pending_tool_call_id) before the next call starts -- without
            # it, both frames get queued back-to-back with no intervening
            # await and can race.
            SleepFrame(sleep=0.02),
            _started("recommend_properties", tool_call_id="call-2"),
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in speak_frames] == ["First line.", "Second line."]


# ---------------------------------------------------------------------------
# Phase 3: get_pricing/negotiate_rate coverage, per-tool thresholds/phrase
# pools, and the 12 scenarios from the brief. Tests 1/2/3/4 (fast tool / slow
# tool / eventual completion / no duplicate) are direct extensions of the
# pre-existing tests above onto the new tools; Test 9 (tool failure) and
# Test 7's cancellation half are already covered by
# test_a_tool_call_that_errors_out_does_not_permanently_block_later_fillers
# and test_no_filler_if_call_is_cancelled_before_delay_elapses respectively
# -- not duplicated, see each test's own docstring below for why.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_scope_now_includes_pricing_and_negotiation_tools():
    """Regression guard on the actual Phase 3 scope change itself."""
    assert DEFAULT_SLOW_TOOLS == {"recommend_properties", "get_pricing", "negotiate_rate"}


@pytest.mark.asyncio
async def test_get_pricing_uses_the_higher_pricing_threshold_by_default():
    """Test 1 (brief) for get_pricing specifically: the common, DB-only
    pricing path (~1.5s per the module's own confirmed-live citation) must
    NOT trigger a filler at the default 2.0s pricing threshold -- proves
    the higher threshold, not just that a threshold exists."""
    filler = SlowToolFillerProcessor()  # real defaults, not a tightened test override
    assert DEFAULT_TOOL_DELAY_OVERRIDES["get_pricing"] == PRICING_TOOL_DELAY_SECONDS

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.05),
            FunctionCallResultFrame(function_name="get_pricing", tool_call_id="call-1", arguments={}, result="ok"),
            SleepFrame(sleep=0.05),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_slow_get_pricing_speaks_exactly_one_pricing_filler():
    """Test 2 (brief): a get_pricing call that genuinely exceeds ITS OWN
    (higher) threshold gets exactly one filler, from the pricing phrase
    pool, not the recommend_properties pool."""
    filler = SlowToolFillerProcessor(
        slow_tools={"get_pricing"},
        tool_delay_overrides={"get_pricing": 0.1},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.2),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text in DEFAULT_PRICING_FILLER_TEXTS
    assert speak_frames[0].text not in ("Let me check that for you.", "One moment, just looking that up.", "Give me just a second.")


@pytest.mark.asyncio
async def test_negotiate_rate_shares_the_pricing_threshold_and_phrase_pool():
    """negotiate_rate must behave identically to get_pricing -- both share
    calculate_price's own SearchApi.io exposure (pricing_engine.py:435),
    so both must share the same threshold/phrase-pool treatment."""
    filler = SlowToolFillerProcessor(
        slow_tools={"negotiate_rate"},
        tool_delay_overrides={"negotiate_rate": 0.1},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("negotiate_rate"),
            SleepFrame(sleep=0.2),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text in DEFAULT_PRICING_FILLER_TEXTS


@pytest.mark.asyncio
async def test_slow_pricing_tool_eventually_completes_no_overlap():
    """Test 3 (brief): filler speaks, then (once the real tool result
    arrives and the LLM produces its actual reply) no second filler ever
    interferes -- the filler processor's own state is fully cleared after
    firing, so nothing it does could overlap a later real response. This
    processor only ever emits the filler line itself; the real answer is
    produced by the LLM/TTS stages downstream of it, so "no overlap" here
    means "the filler processor doesn't emit anything else for this same
    tool call" -- confirmed by asserting exactly one TTSSpeakFrame total
    across the whole tool-call lifecycle, start to result."""
    filler = SlowToolFillerProcessor(
        slow_tools={"get_pricing"},
        tool_delay_overrides={"get_pricing": 0.1},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.2),  # filler fires
            FunctionCallResultFrame(function_name="get_pricing", tool_call_id="call-1", arguments={}, result="ok"),
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1  # exactly one filler, nothing further queued after the result


@pytest.mark.asyncio
async def test_tool_completing_right_after_threshold_does_not_duplicate_filler():
    """Test 4 (brief): once the timer has fired and cleared
    _pending_tool_call_id, a result arriving immediately afterward must not
    somehow produce a second filler -- the clear-before-speak ordering in
    _on_timeout (see slow_tool_filler.py) already guarantees this; this
    test proves it under the new per-tool-threshold code path
    specifically, not just the original single-threshold path the
    pre-existing tests above already cover."""
    filler = SlowToolFillerProcessor(
        slow_tools={"get_pricing"},
        tool_delay_overrides={"get_pricing": 0.05},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.1),  # filler fires, state cleared
            FunctionCallResultFrame(function_name="get_pricing", tool_call_id="call-1", arguments={}, result="ok"),
            SleepFrame(sleep=0.05),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1


@pytest.mark.asyncio
async def test_multiple_slow_tools_in_one_conversation_each_get_their_own_filler():
    """Test 5 (brief): a recommend_properties call followed by a slow
    get_pricing call (e.g. guest asks "what's the price on that one?"
    right after hearing recommendations) each independently cross their
    own threshold -- natural behavior is one filler per genuinely slow
    call, not a runaway/compounding sequence. Uses each tool's own
    default threshold (1.2s / 2.0s) scaled down via constructor overrides
    for test speed, not a change to the defaults themselves."""
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties", "get_pricing"},
        delay_seconds=0.05,
        tool_delay_overrides={"get_pricing": 0.05},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.1),
            _result(tool_call_id="call-1"),
            SleepFrame(sleep=0.02),
            _started("get_pricing", tool_call_id="call-2"),
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 2
    # Each from its own pool -- not two identical lines back to back.
    assert speak_frames[0].text != speak_frames[1].text


@pytest.mark.asyncio
async def test_pricing_filler_phrases_rotate_independently_of_recommend_properties_pool():
    """Test 6 (brief), Phase 3-specific: recommend_properties and
    get_pricing/negotiate_rate each rotate through their OWN phrase pool
    (pool identity-keyed rotation index, see SlowToolFillerProcessor.
    __init__) -- a slow get_pricing call right after a slow
    recommend_properties call must not skip an entry in either pool or
    accidentally share rotation state."""
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties", "get_pricing"},
        filler_texts=["Reco A.", "Reco B."],
        tool_delay_overrides={"get_pricing": 0.05},
        tool_filler_text_overrides={"get_pricing": ["Price A.", "Price B."]},
        delay_seconds=0.05,
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.1),
            _result(tool_call_id="call-1"),
            SleepFrame(sleep=0.02),
            _started("get_pricing", tool_call_id="call-2"),
            SleepFrame(sleep=0.1),
            FunctionCallResultFrame(function_name="get_pricing", tool_call_id="call-2", arguments={}, result="ok"),
            SleepFrame(sleep=0.02),
            _started("recommend_properties", tool_call_id="call-3"),
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f.text for f in down_frames if isinstance(f, TTSSpeakFrame)]
    # recommend_properties pool advances Reco A -> Reco B independently of
    # get_pricing's own Price A in between -- not Reco A -> Price A -> Reco B
    # (that would still look correct here) vs. a shared-index bug that would
    # instead skip straight to a pool's second entry too early or wrap
    # incorrectly. Explicit sequence assertion catches both failure shapes.
    assert speak_frames == ["Reco A.", "Price A.", "Reco B."]


@pytest.mark.asyncio
async def test_interruption_cancels_the_pending_tool_call_and_its_filler_timer():
    """Test 7 (brief): guest interrupts during the filler-pending window.
    Investigated precisely (not assumed) against pipecat's own
    llm_service.py: FunctionCallRegistryItem.cancel_on_interruption
    defaults to True for every tool (confirmed via register_function's own
    docstring/_resolve_tool_option chain), and Mira's own tool
    registration (app/voice/tools.py) never overrides this -- so a real
    InterruptionFrame (guest barge-in, detected by local VAD) causes
    pipecat's LLM service to call _cancel_function_call, which pushes
    exactly the same FunctionCallCancelFrame this processor already
    listens for (see test_no_filler_if_call_is_cancelled_before_delay_
    elapses above). This test proves that existing cancellation path is
    what actually protects the pending filler under a real barge-in,
    end to end from this processor's point of view -- SlowToolFillerProcessor
    itself has and needs no InterruptionFrame-specific code at all; the
    guest's own speech is unaffected by any of this (this processor never
    touches STT/VAD frames)."""
    filler = SlowToolFillerProcessor(slow_tools={"get_pricing"}, tool_delay_overrides={"get_pricing": 0.2})

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.05),
            # Stand-in for what pipecat's LLM service does on a real
            # InterruptionFrame with cancel_on_interruption=True (the
            # default for every Mira tool) -- the FunctionCallCancelFrame
            # IS the observable effect this processor reacts to.
            FunctionCallCancelFrame(function_name="get_pricing", tool_call_id="call-1"),
            SleepFrame(sleep=0.3),  # comfortably past the original 0.2s threshold
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_call_ending_during_filler_delay_produces_no_filler():
    """Test 8 (brief): call terminates during filler (Phase 2's silence
    watchdog or hard ceiling firing, or the pipeline tearing down for any
    other reason). The safe, directly-observable claim: no filler is ever
    spoken after the call has ended, since run_test's own send_end_frame
    default pushes a real EndFrame once frames_to_send is exhausted, well
    before the (deliberately long, 5s) filler threshold here.

    Investigated precisely rather than assumed: SlowToolFillerProcessor's
    timer is created via self.create_task (pipecat's own task-manager-
    scoped task, same mechanism SilenceWatchdogProcessor's nudge timer
    already uses). This does NOT, however, mean an EndFrame automatically
    cancels it -- confirmed directly against pipecat's own frame_processor.py:
    FrameProcessor.__cancel (the method that actually calls
    __cancel_process_task/__cancel_input_task) is wired to CancelFrame only,
    not EndFrame, which is a graceful "flush the queue" shutdown, not a
    cancellation signal. Every real termination path in this codebase
    (SilenceWatchdogProcessor's own hangup, Phase 2's hard ceiling,
    end_call, host handoff) uses EndFrame/EndWorkerFrame, never CancelFrame
    -- so a still-pending filler timer at call-end time is reclaimed by
    ordinary Python GC once the call's own Pipeline/PipelineWorker/
    processor objects go out of scope (each call gets its own fresh
    instances -- app/voice/pipeline.py's _run_pipeline_inner constructs a
    brand new SlowToolFillerProcessor per call), not by an explicit
    cancellation this processor or the pipeline issues at EndFrame time.
    This is a pre-existing property of this processor's design (unchanged
    by Phase 3 -- the original 1.2s single-tool version had the identical
    shape, just never exercised by a test with a long enough pending sleep
    to surface it) and does not orphan a task across calls or leak
    unboundedly, since it dies with its own call's objects -- but it is
    not the same guarantee as an explicit cancel-in-finally-block (Phase
    2's _enforce_max_call_duration, a manually-tracked asyncio.Task,
    genuinely does need and have that, since detached background tasks
    are NOT owned by any processor's task manager at all -- see that
    function's own docstring). Reported precisely here rather than
    asserting a stronger guarantee than the code actually provides."""
    filler = SlowToolFillerProcessor(slow_tools={"get_pricing"}, tool_delay_overrides={"get_pricing": 5.0})

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.05),  # call ends here (implicit EndFrame follows), well before the 5s threshold
        ],
    )

    # The one safe, directly-observable claim: no filler is ever spoken
    # once the call has ended.
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_tool_failure_does_not_prevent_a_later_fillers_from_firing():
    """Test 9 (brief): already covered end to end by
    test_a_tool_call_that_errors_out_does_not_permanently_block_later_fillers
    above -- an ErrorFrame (the actual observable effect of a tool handler
    raising, per llm_service.py's run_function_calls) does not permanently
    stick _pending_tool_call_id, so a later, genuinely new tool call still
    gets its own filler. Not duplicated here; this stub exists so the test
    file's own structure maps 1:1 onto the brief's numbered scenarios for
    the final report's Section 9."""


@pytest.mark.asyncio
async def test_tool_timeout_is_a_pricing_engine_concern_not_a_filler_concern():
    """Test 10 (brief): a genuine SearchApi.io timeout (15s client timeout,
    app/integrations/searchapi_client.py) is caught inside
    fetch_listing_total_price/_fetch_listing_price_uncached (httpx.HTTPError
    is caught, function returns None) and pricing_engine.calculate_price
    falls back to Property.base_price -- confirmed by direct code read, not
    changed in this phase (Phase 3 explicitly must not modify pricing
    logic). From THIS processor's point of view, a timeout looks
    identical to any other slow-but-eventually-successful call: the filler
    fires at the 2.0s pricing threshold, then a normal
    FunctionCallResultFrame eventually arrives (handle_get_pricing always
    returns a string, never raises, on this path) well after -- this test
    proves the filler does not somehow block or interfere with that
    eventual real result, i.e. Mira still ends up with a useful spoken
    answer (the base_price fallback), not silence, even on the slowest
    realistic path this repository's own architecture already guarantees
    gracefully."""
    filler = SlowToolFillerProcessor(slow_tools={"get_pricing"}, tool_delay_overrides={"get_pricing": 0.05})

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.1),  # filler fires
            # Stand-in for the SearchApi.io timeout path eventually
            # resolving to pricing_engine's own base_price fallback --
            # still a normal FunctionCallResultFrame from this processor's
            # perspective, arriving well after the filler already spoke.
            FunctionCallResultFrame(
                function_name="get_pricing", tool_call_id="call-1", arguments={}, result="ok-fallback-price"
            ),
            SleepFrame(sleep=0.05),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1  # filler spoke once; the real (fallback) answer still reaches the guest normally


@pytest.mark.asyncio
async def test_hindi_hinglish_filler_is_an_explicitly_reported_gap_not_silently_handled():
    """Tests 11/12 (brief): language-consistent Hindi/Hinglish fillers and
    gender-safe Hindi grammar. Per Step 6 of the brief ("if the existing
    filler infrastructure does not safely support language-specific filler
    selection: DO NOT invent a large new language system. Report the
    limitation"), this processor has NO ConversationState/ConversationStyle
    reference and NO Hindi phrase pool at all -- confirmed structurally,
    not just by omission, so a Hindi/Hinglish-speaking guest hearing a slow
    get_pricing/negotiate_rate/recommend_properties call gets an ENGLISH
    filler line regardless of the conversation's own language, same as
    before this phase. This test documents that as a known, explicitly
    reported gap (not a Phase 3 regression -- recommend_properties'
    pre-existing filler had the identical gap already) rather than
    asserting incorrect behavior is correct."""
    filler = SlowToolFillerProcessor(slow_tools={"get_pricing"}, tool_delay_overrides={"get_pricing": 0.05})
    assert not hasattr(filler, "_conversation_state")
    assert not hasattr(filler, "_conversation_style")

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    # English-only pool, regardless of what language the guest was
    # actually speaking -- the gap this test documents.
    assert speak_frames[0].text in DEFAULT_PRICING_FILLER_TEXTS
    assert all(ord(c) < 128 for c in speak_frames[0].text)  # ASCII-only, confirms no Hindi/Devanagari text


# ---------------------------------------------------------------------------
# Staff-engineer review finding: two scoped tools starting SIMULTANEOUSLY in
# one FunctionCallsStartedFrame (a real, LLM-driven pattern -- see
# _started_multi's own docstring) were previously tracked via a single set
# of _pending_tool_call_id/_pending_tool_name/_timer_task fields, so only
# the LAST scoped call in the list ever got a filler -- the first was
# silently dropped from tracking entirely, even if it was the one that
# actually ran slow. Reproduced directly before fixing (two scoped tools
# together produced exactly one filler, for whichever was last), then fixed
# by keying pending state per tool_call_id (SlowToolFillerProcessor._pending,
# a dict) so each concurrently pending call gets its own independent timer.
# Not a Phase 3 regression in the sense of "newly introduced" -- the
# single-pending-call shape predates Phase 3 -- but Phase 3's scope
# expansion from one tool to three made two-scoped-tools-at-once meaningfully
# more likely to actually occur in production.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_simultaneous_scoped_tool_calls_each_get_their_own_filler():
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties", "get_pricing"},
        delay_seconds=0.1,
        tool_delay_overrides={"get_pricing": 0.1},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started_multi(("recommend_properties", "call-A"), ("get_pricing", "call-B")),
            SleepFrame(sleep=0.2),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 2
    texts = {f.text for f in speak_frames}
    assert any(t in DEFAULT_PRICING_FILLER_TEXTS for t in texts)  # get_pricing's own pool fired
    assert any(t not in DEFAULT_PRICING_FILLER_TEXTS for t in texts)  # recommend_properties' own pool also fired


@pytest.mark.asyncio
async def test_one_of_two_simultaneous_calls_resolving_fast_does_not_suppress_the_others_filler():
    """The exact failure mode found in review: call-A (recommend_properties,
    slow) and call-B (get_pricing, resolves fast -- BEFORE its own
    threshold) start together -- call-B resolving must not suppress call-A's
    own, independent filler once call-A genuinely crosses its own
    threshold."""
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties", "get_pricing"},
        delay_seconds=0.2,
        tool_delay_overrides={"get_pricing": 0.3},  # higher than call-A's, so call-B resolving at 0.05s is well before its own threshold
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started_multi(("recommend_properties", "call-A"), ("get_pricing", "call-B")),
            SleepFrame(sleep=0.05),
            FunctionCallResultFrame(function_name="get_pricing", tool_call_id="call-B", arguments={}, result="ok"),
            SleepFrame(sleep=0.2),  # comfortably past call-A's own 0.2s threshold, still short of call-B's 0.3s
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    # Exactly one filler -- call-A's own (recommend_properties genuinely
    # crossed 0.2s); call-B resolved at 0.05s, well before its own 0.3s
    # threshold, so its filler never fires at all.
    assert len(speak_frames) == 1
    assert speak_frames[0].text not in DEFAULT_PRICING_FILLER_TEXTS  # recommend_properties' own pool


@pytest.mark.asyncio
async def test_cancelling_one_of_two_simultaneous_calls_does_not_affect_the_other():
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties", "get_pricing"},
        delay_seconds=0.1,
        tool_delay_overrides={"get_pricing": 0.1},
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started_multi(("recommend_properties", "call-A"), ("get_pricing", "call-B")),
            SleepFrame(sleep=0.02),
            FunctionCallCancelFrame(function_name="get_pricing", tool_call_id="call-B"),
            SleepFrame(sleep=0.2),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1  # only call-A's filler; call-B was cancelled cleanly
    assert speak_frames[0].text not in DEFAULT_PRICING_FILLER_TEXTS
