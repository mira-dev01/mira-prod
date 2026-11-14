"""The per-call scorecard (app/services/call_scorecard.py): a call that went wrong is flagged, a clean one is not.

The first transcript is the shape of test call 56947e99 (2026-11-13): Mira asked one question four times because
the guest's answers had been replaced by an apology. It must be flagged on the stored transcript alone.
"""

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
)
from sqlalchemy import select

from app.models.call_quality_event import CALL_SCORECARD_RULE, INTERNAL_TELEMETRY_RULES, CallQualityEvent
from app.services import call_scorecard
from app.services.call_scorecard import build_scorecard, end_label, parse_transcript, repeated_question, top_reason
from app.voice.call_metrics import CallMetricsCollector

GROQ = ["openai/gpt-oss-120b"]

FOUR_TIMES = """assistant: Hello, this is Mira from Jugglers. How can I help you today?
user: Do you have a place in Indiranagar
assistant: Which area are you looking at, and for how many nights?
user: Please say that again.
assistant: Which area are you looking at, and for how many nights?
user: Please say that again.
assistant: Which area are you looking in, and for how many nights?
user: Please say that again.
assistant: Which area are you looking at, and for how many nights?"""

CLEAN = """assistant: Hello, this is Mira. How can I help you today?
user: Looking for a villa in Goa for four people
assistant: Lovely. Which dates are you thinking of?
user: Next weekend
assistant: Great, I have two options. Would you like the details?
user: Yes please"""


# The assistant's questions from test call 56947e99, exactly as stored (the guest's side is left out: it carries a
# personal name). One question, asked five times, in five Hindi phrasings.
REAL_HINDI_QUESTIONS = [
    "क्या आप बेंगलुरु के किसी और पड़ोस, जैसे कोरमंगला या जुहू, में देखना चाहेंगे?",
    "आप इनमें से किसी एक में देखना चाहेंगे?",
    "इनमें से कौन‑सा क्षेत्र आपके लिए ठीक रहेगा?",
    "आप इनमें से किस क्षेत्र में देखना चाहेंगे?",
    "आप किस जगह को पसंद करेंगे?",
]


def _summary(**overrides):
    base = {
        "end_frame": "EndFrame", "end_reason": None, "user_turns": 3, "tool_calls": {"recommend_properties": 1},
        "tool_errors": {}, "tool_no_results": {}, "response_latency_s": [1.2, 0.9],
        "seconds_from_last_bot_start_to_end": 30.0,
    }
    base.update(overrides)
    return base


def _codes(card):
    return [flag["code"] for flag in card["flags"]]


# --- the call that started this ---


def test_the_test_call_is_flagged_for_the_repeated_question_from_its_stored_transcript_alone():
    card = build_scorecard(transcript=FOUR_TIMES, metrics_summary=None)

    assert card["flagged"] is True
    assert _codes(card) == ["repeated_question"]
    assert card["repeated_question_repeats"] == 3
    assert "Which area are you looking" in card["flags"][0]["detail"]
    assert "asked 4 times" in card["flags"][0]["detail"]


def test_a_clean_call_is_not_flagged():
    card = build_scorecard(transcript=CLEAN, metrics_summary=_summary())

    assert card["flagged"] is False and card["flags"] == []
    assert card["ended_by"] == "mira_ended"
    assert card["longest_response_gap_s"] == 1.2


def test_the_real_calls_reworded_hindi_question_is_caught():
    transcript = "\n".join(f"assistant: बेंगलुरु में कई विकल्प हैं। {q}\nuser: कहाँ है" for q in REAL_HINDI_QUESTIONS)
    card = build_scorecard(transcript=transcript, metrics_summary=None)
    assert _codes(card) == ["repeated_question"]
    assert card["repeated_question_repeats"] >= 3


@pytest.mark.parametrize(
    "questions",
    [
        ["How many guests will be staying with us?", "How many nights are you planning to stay?", "How many rooms would you like to book?"],
        ["Which dates are you looking at for the stay?", "Which area are you looking at for the stay?", "Would you like a place with a pool?"],
        ["आपके समूह में कितने लोग रहेंगे?", "आप कितनी रातें ठहरना चाहेंगे?", "आपको कौन सी तारीख़ें चाहिए?"],
    ],
)
def test_different_questions_that_share_a_shape_are_not_one_repeated_question(questions):
    transcript = "\n".join(f"assistant: {q}\nuser: okay" for q in questions)
    assert build_scorecard(transcript=transcript, metrics_summary=None)["flagged"] is False


# --- each rule, on its own boundary ---


def test_a_question_asked_twice_is_ordinary_but_three_times_is_flagged():
    twice = "assistant: Which dates are you thinking of for the stay?\nuser: hmm\nassistant: Which dates are you thinking of for the stay?"
    assert build_scorecard(transcript=twice, metrics_summary=None)["flagged"] is False
    thrice = twice + "\nuser: hmm\nassistant: Which dates are you thinking of for the stay?"
    assert _codes(build_scorecard(transcript=thrice, metrics_summary=None)) == ["repeated_question"]


def test_a_short_filler_question_repeated_is_not_the_failure_this_finds():
    text = "\n".join(["assistant: Okay?", "user: yes"] * 5)
    assert repeated_question(parse_transcript(text)) == (0, "")


def test_different_questions_are_not_repeats():
    text = (
        "assistant: Which dates are you thinking of for the stay?\nuser: friday\n"
        "assistant: How many guests will be travelling with you?\nuser: four\n"
        "assistant: Would you like a place with a swimming pool?"
    )
    assert repeated_question(parse_transcript(text))[0] == 0


def test_two_inaudible_turns_are_flagged_one_is_not():
    one = CLEAN + "\nuser: [inaudible]\nassistant: Sorry, could you say that again?"
    assert build_scorecard(transcript=one, metrics_summary=_summary())["flagged"] is False
    two = one + "\nuser: [inaudible]\nassistant: Sorry, I missed that, once more?"
    card = build_scorecard(transcript=two, metrics_summary=_summary())
    assert card["inaudible_turns"] == 2 and _codes(card) == ["inaudible_turns"]


def test_any_tool_error_is_flagged_but_no_results_alone_is_only_recorded():
    errored = build_scorecard(transcript=CLEAN, metrics_summary=_summary(tool_errors={"get_pricing": 1}))
    assert _codes(errored) == ["tool_error"] and "get_pricing" in errored["flags"][0]["detail"]

    empty = build_scorecard(transcript=CLEAN, metrics_summary=_summary(tool_no_results={"recommend_properties": 2}))
    assert empty["flagged"] is False
    assert empty["tool_no_results"] == {"recommend_properties": 2}


@pytest.mark.parametrize(
    ("end_frame", "after_bot", "flagged"),
    [
        ("CancelFrame", 6.0, True),    # guest hung up 6 s after Mira began speaking
        ("CancelFrame", 10.0, True),   # the boundary counts
        ("CancelFrame", 10.5, False),
        ("CancelFrame", 45.0, False),  # a long call that the guest ended
        ("EndFrame", 3.0, False),      # Mira ended it herself (a normal close)
        ("CancelFrame", None, False),  # no timing recorded: never a reason to flag
    ],
)
def test_a_guest_hanging_up_right_after_mira_spoke_is_flagged(end_frame, after_bot, flagged):
    card = build_scorecard(
        transcript=CLEAN, metrics_summary=_summary(end_frame=end_frame, seconds_from_last_bot_start_to_end=after_bot)
    )
    assert card["flagged"] is flagged
    if flagged:
        assert _codes(card) == ["quick_hangup"]


@pytest.mark.parametrize(("gap", "flagged"), [(3.0, False), (3.01, True), (7.5, True)])
def test_a_response_gap_over_three_seconds_is_flagged(gap, flagged):
    card = build_scorecard(transcript=CLEAN, metrics_summary=_summary(response_latency_s=[0.8, gap]))
    assert card["flagged"] is flagged
    assert card["longest_response_gap_s"] == gap


def test_several_problems_are_all_reported():
    card = build_scorecard(
        transcript=FOUR_TIMES + "\nuser: [inaudible]\nassistant: Say again?\nuser: [inaudible]",
        metrics_summary=_summary(tool_errors={"get_pricing": 1}, response_latency_s=[5.0]),
    )
    assert set(_codes(card)) == {"repeated_question", "inaudible_turns", "tool_error", "slow_response"}


# --- parsing and small pieces ---


def test_a_multi_line_turn_stays_one_turn():
    turns = parse_transcript("assistant: Here are two options:\n1. Loft\n2. Villa\nuser: the loft")
    assert turns == [("assistant", "Here are two options: 1. Loft 2. Villa"), ("user", "the loft")]


def test_a_call_with_nothing_stored_scores_clean_without_crashing():
    card = build_scorecard(transcript=None, metrics_summary=None)
    assert card["flagged"] is False and card["ended_by"] is None and card["longest_response_gap_s"] is None


@pytest.mark.parametrize(
    ("frame", "reason", "label"),
    [
        ("EndFrame", None, "mira_ended"),
        ("CancelFrame", None, "guest_hung_up"),
        ("EndFrame", "silent caller", "silent_caller"),
        ("CancelFrame", "host_handoff", "host_handoff"),
        ("CancelFrame", "max_call_duration_exceeded", "max_duration"),
    ],
)
def test_who_ended_the_call(frame, reason, label):
    assert end_label(frame, reason) == label


def test_top_reason_is_the_most_common_flag_with_a_stable_tie_break():
    cards = [
        {"flags": [{"code": "tool_error"}, {"code": "slow_response"}]},
        {"flags": [{"code": "slow_response"}]},
        {"flags": [{"code": "tool_error"}]},
        {"flags": []},
    ]
    assert top_reason(cards) == ("slow_response", 2)  # tied with tool_error: alphabetical
    assert top_reason([{"flags": []}]) is None


def test_the_scorecard_row_is_internal_telemetry_so_host_analytics_ignore_it():
    assert CALL_SCORECARD_RULE in INTERNAL_TELEMETRY_RULES


# --- the collector's new inputs ---


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _collector(clock):
    return CallMetricsCollector(groq_models=GROQ, openrouter_model="m", tts_model="t", clock=clock)


def _finalize(collector):
    return collector.finalize(end_frame="CancelFrame", end_reason=None, stt_audio_seconds=None, stt_model=None)[0].metadata["summary"]


def test_the_collector_counts_no_results_separately_from_errors():
    c = _collector(_Clock())
    for call_id, result in (("a", {"status": "no_results"}), ("b", '{"status": "no_results"}'), ("c", {"status": "ok"}), ("d", {"error": "x"})):
        c._observe(FunctionCallInProgressFrame(function_name="recommend_properties", tool_call_id=call_id, arguments={}))
        c._observe(FunctionCallResultFrame(function_name="recommend_properties", tool_call_id=call_id, arguments={}, result=result))

    summary = _finalize(c)

    assert summary["tool_no_results"] == {"recommend_properties": 2}
    assert summary["tool_errors"] == {"recommend_properties": 1}


def test_the_collector_reports_how_long_before_the_end_mira_last_started_speaking():
    clock = _Clock()
    c = _collector(clock)
    c._observe(BotStartedSpeakingFrame())
    clock.now += 4.0
    c._observe(BotStartedSpeakingFrame())
    clock.now += 6.5

    assert _finalize(c)["seconds_from_last_bot_start_to_end"] == 6.5  # from the LAST time she started, not the first


def test_a_call_where_mira_never_spoke_has_no_hangup_timing():
    assert _finalize(_collector(_Clock()))["seconds_from_last_bot_start_to_end"] is None


# --- the end time, the order of the steps, and what is stored ---


def test_the_end_time_is_taken_when_the_call_ends_not_when_the_post_call_work_finishes():
    """finalize() runs after the classification and summary LLM calls, seconds later. The hang-up timing must be
    measured to the moment the terminal frame passed, or "hung up within 10 s" could never fire."""
    clock = _Clock()
    c = _collector(clock)
    c._observe(BotStartedSpeakingFrame())
    clock.now += 6.0
    c._observe(CancelFrame())
    clock.now += 20.0  # post-call work

    assert _finalize(c)["seconds_from_last_bot_start_to_end"] == 6.0


async def test_the_terminal_frame_really_reaches_the_collector_in_a_running_pipeline():
    """Unit tests feed frames straight to the collector; this proves a real pipeline run delivers the end of
    the call to it (pipecat appends an EndFrame when the test pipeline finishes), and that it ends up in the summary."""
    from pipecat.tests.utils import run_test

    collector = _collector(_Clock())

    await run_test(collector, frames_to_send=[BotStartedSpeakingFrame()])

    assert collector._ended_at is not None
    assert _finalize(collector)["seconds_from_last_bot_start_to_end"] == 0.0


def test_only_the_first_terminal_frame_counts_as_the_end():
    clock = _Clock()
    c = _collector(clock)
    c._observe(BotStartedSpeakingFrame())
    clock.now += 4.0
    c._observe(EndFrame())
    clock.now += 3.0
    c._observe(CancelFrame())

    assert _finalize(c)["seconds_from_last_bot_start_to_end"] == 4.0


def test_the_scorecard_is_the_last_post_call_step_so_it_sees_the_leads_that_survive_cleanup():
    import inspect

    from app.voice import pipeline as voice_pipeline

    source = inspect.getsource(voice_pipeline._run_pipeline_inner)
    for earlier in ("persist_telemetry", "lead_backfill_or_cleanup", "unqualified_lead_cleanup"):
        assert source.index(f'steps.step("{earlier}"') < source.index('steps.step("call_scorecard"'), earlier
    assert source.index('steps.step("call_scorecard"') < source.index("steps.log_summary()")
    assert "low_confidence_transcript_guard.decision_counts" in source


async def test_the_outcome_is_what_the_call_really_led_to(db_session, test_call_session):
    assert (await call_scorecard.load_outcome(db_session, test_call_session.id))["lead"] is False

    from app.models.lead import Lead

    lead = Lead(user_id=test_call_session.user_id, call_session_id=test_call_session.id, guest_name="x", phone="+919999999999")
    db_session.add(lead)
    await db_session.flush()
    test_call_session.lead_id = lead.id
    await db_session.commit()

    outcome = await call_scorecard.load_outcome(db_session, test_call_session.id)
    assert outcome["lead"] is True and outcome["booking"] is False and outcome["escalation"] is False


def _finished(clock, bot_started_ago=4.0):
    metrics = _collector(clock)
    metrics._observe(BotStartedSpeakingFrame())
    clock.now += bot_started_ago
    metrics._observe(CancelFrame())
    _finalize(metrics)
    return metrics


async def test_recording_a_call_stores_a_flagged_scorecard_that_can_be_rescored(db_session, test_call_session):
    from app.voice import pipeline as voice_pipeline

    metrics = _finished(_Clock())

    await voice_pipeline._record_scorecard(db_session, test_call_session.id, metrics, FOUR_TIMES, {"pass": 3, "pass_flagged": 1})

    rows = (await db_session.scalars(select(CallQualityEvent).where(CallQualityEvent.call_session_id == test_call_session.id))).all()
    row = next(r for r in rows if r.rule == CALL_SCORECARD_RULE)
    card = row.metadata_json["scorecard"]
    assert row.severity == "WARNING"
    assert card["flagged"] is True and set(_codes(card)) == {"repeated_question", "quick_hangup"}
    assert card["guard_decisions"] == {"pass": 3, "pass_flagged": 1}
    assert card["ended_by"] == "guest_hung_up" and card["seconds_from_last_bot_start_to_end"] == 4.0
    assert card["outcome"]["escalation"] is False

    # Re-scoring from the same inputs gives the same answer.
    assert _codes(build_scorecard(transcript=FOUR_TIMES, metrics_summary=metrics.summary)) == _codes(card)


async def test_a_clean_call_is_stored_unflagged(db_session, test_call_session):
    from app.voice import pipeline as voice_pipeline

    clock = _Clock()
    metrics = _collector(clock)
    metrics._observe(BotStartedSpeakingFrame())
    clock.now += 60.0
    metrics._observe(EndFrame())  # Mira closed the call herself, a minute after speaking
    _finalize(metrics)

    await voice_pipeline._record_scorecard(db_session, test_call_session.id, metrics, CLEAN, None)

    row = (await db_session.scalars(select(CallQualityEvent).where(CallQualityEvent.rule == CALL_SCORECARD_RULE))).one()
    assert row.severity == "INFO" and row.metadata_json["scorecard"]["flagged"] is False


async def test_a_scoring_failure_never_breaks_teardown(db_session, test_call_session, monkeypatch):
    from app.voice import pipeline as voice_pipeline

    def boom(**kwargs):
        raise RuntimeError("scorer broke")

    monkeypatch.setattr(call_scorecard, "build_scorecard", boom)

    await voice_pipeline._record_scorecard(db_session, test_call_session.id, _finished(_Clock()), CLEAN, None)  # does not raise

    assert (await db_session.scalars(select(CallQualityEvent).where(CallQualityEvent.rule == CALL_SCORECARD_RULE))).all() == []


async def test_a_call_with_no_session_is_skipped(db_session):
    from app.voice import pipeline as voice_pipeline

    await voice_pipeline._record_scorecard(db_session, None, _finished(_Clock()), CLEAN, None)  # does not raise
