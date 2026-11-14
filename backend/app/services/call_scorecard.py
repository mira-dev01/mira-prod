"""A per-call scorecard: did this call go wrong, and why, without anyone phoning in to find out.

Built from what a call already leaves behind -- the transcript, the usage/performance summary
(app/voice/call_metrics.py), the low-confidence guard's decision counts and what the call led to --
so the same function scores a call as it ends and, later, from its stored rows (scripts/rescore_call.py).
Pure except `load_outcome`; stored as one `call_scorecard` row in call_quality_events (an internal
telemetry rule: no migration, and host-facing analytics ignore it).

A call is FLAGGED when any of these holds (thresholds below, from docs/tasks/call-fluency-fixes.md Task 15):
  * repeated_question   -- Mira asked the same question again and again (>= 2 repeats);
  * inaudible_turns     -- two or more guest turns became "[inaudible]";
  * tool_error          -- any tool failed;
  * quick_hangup        -- the guest hung up within 10 s of Mira starting to speak;
  * slow_response       -- Mira took over 3 s to start answering at least once.
"""

import re
import uuid
from collections import Counter
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.voice.low_confidence_transcript_guard import INAUDIBLE_MARKER

# "an assistant question repeated twice or more": the same question asked again at least twice
# (so three times in all). Asking once more because the guest did not hear is ordinary.
REPEATED_QUESTION_MIN_REPEATS = 2
INAUDIBLE_TURNS_MIN = 2
QUICK_HANGUP_WITHIN_SECONDS = 10.0
SLOW_RESPONSE_SECONDS = 3.0

# Two questions are "the same" when their wording is almost identical, or they share a third of their words.
# The word-overlap rule is what catches the real failure: on test call 56947e99 Mira asked one thing four times
# in four different Hindi phrasings ("...किसी एक में देखना चाहेंगे?", "...किस क्षेत्र में देखना चाहेंगे?"), which
# spelling similarity alone scores as different. Chosen on that call and on distinct questions that merely share
# a shape ("How many guests...?" / "How many nights...?"), which stay below it.
_SAME_QUESTION_RATIO = 0.85
_SAME_QUESTION_WORD_OVERLAP = 0.33
_MIN_QUESTION_CHARS = 12  # "Okay?" repeated is not the failure this finds
_ROLE_LINE = re.compile(r"^(user|assistant|system|tool):\s?(.*)$")
_SENTENCE_END = re.compile(r"(?<=[.?!।])\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)

REASON_LABELS = {
    "repeated_question": "Mira repeated the same question",
    "inaudible_turns": "Several guest turns were unclear audio",
    "tool_error": "A tool failed",
    "quick_hangup": "Guest hung up right after Mira spoke",
    "slow_response": "Mira was slow to answer",
}


def end_label(end_frame: str | None, end_reason: str | None) -> str:
    """Who or what ended the call, from the pipeline's terminal frame."""
    if end_reason == "max_call_duration_exceeded":
        return "max_duration"
    if end_reason == "host_handoff":
        return "host_handoff"
    if end_reason == "silent caller":
        return "silent_caller"
    if end_frame == "CancelFrame":
        return "guest_hung_up"
    return "mira_ended"


def parse_transcript(transcript: str | None) -> list[tuple[str, str]]:
    """[(role, text)] from the stored "role: text" lines; a line with no role continues the previous turn."""
    turns: list[tuple[str, str]] = []
    for line in (transcript or "").splitlines():
        match = _ROLE_LINE.match(line)
        if match:
            turns.append((match.group(1), match.group(2).strip()))
        elif turns and line.strip():
            role, text = turns[-1]
            turns[-1] = (role, f"{text} {line.strip()}")
    return turns


def _normalize(question: str) -> str:
    return " ".join(_NON_WORD.sub(" ", question.lower()).split())


def assistant_questions(turns: list[tuple[str, str]]) -> list[str]:
    questions = []
    for role, text in turns:
        if role != "assistant":
            continue
        questions += [s.strip() for s in _SENTENCE_END.split(text) if s.strip().endswith("?")]
    return questions


def _same_question(a: str, b: str) -> bool:
    if a == b or SequenceMatcher(None, a, b).ratio() >= _SAME_QUESTION_RATIO:
        return True
    words_a, words_b = set(a.split()), set(b.split())
    return bool(words_a | words_b) and len(words_a & words_b) / len(words_a | words_b) >= _SAME_QUESTION_WORD_OVERLAP


def repeated_question(turns: list[tuple[str, str]]) -> tuple[int, str]:
    """(how many times the most-repeated question was asked AGAIN, an example of it). Questions are grouped
    when any one of a group is the same as another (`_same_question`), so a question reworded a little more
    each time is still one question."""
    questions = [(q, _normalize(q)) for q in assistant_questions(turns)]
    questions = [(q, key) for q, key in questions if len(key) >= _MIN_QUESTION_CHARS]
    parent = list(range(len(questions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(questions)):
        for j in range(i):
            if _same_question(questions[j][1], questions[i][1]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(questions)):
        groups.setdefault(find(i), []).append(i)
    if not groups:
        return 0, ""
    worst = max(groups.values(), key=len)
    return len(worst) - 1, questions[worst[0]][0][:160]


def inaudible_turn_count(turns: list[tuple[str, str]]) -> int:
    return sum(1 for role, text in turns if role == "user" and text == INAUDIBLE_MARKER)


def build_scorecard(
    *,
    transcript: str | None,
    metrics_summary: dict | None,
    guard_decisions: dict[str, int] | None = None,
    outcome: dict | None = None,
) -> dict:
    """The scorecard for one call. `metrics_summary` is the call_metrics summary (None for a call that
    predates it); `guard_decisions` the low-confidence guard's counts; `outcome` from `load_outcome`.
    Anything unknown stays None/absent rather than being guessed, and is never a reason to flag."""
    summary = metrics_summary or {}
    turns = parse_transcript(transcript)
    repeats, repeated_example = repeated_question(turns)
    inaudible = inaudible_turn_count(turns)
    latencies = [float(v) for v in summary.get("response_latency_s") or []]
    longest_gap = max(latencies) if latencies else None
    tool_calls = dict(summary.get("tool_calls") or {})
    tool_errors = {k: v for k, v in (summary.get("tool_errors") or {}).items() if v}
    tool_no_results = {k: v for k, v in (summary.get("tool_no_results") or {}).items() if v}
    ended_by = end_label(summary.get("end_frame"), summary.get("end_reason")) if summary else None
    after_bot = summary.get("seconds_from_last_bot_start_to_end")

    flags: list[dict] = []
    if repeats >= REPEATED_QUESTION_MIN_REPEATS:
        flags.append({"code": "repeated_question", "detail": f"asked {repeats + 1} times: {repeated_example}"})
    if inaudible >= INAUDIBLE_TURNS_MIN:
        flags.append({"code": "inaudible_turns", "detail": f"{inaudible} guest turns"})
    if tool_errors:
        flags.append({"code": "tool_error", "detail": ", ".join(f"{k} x{v}" for k, v in sorted(tool_errors.items()))})
    if ended_by == "guest_hung_up" and after_bot is not None and float(after_bot) <= QUICK_HANGUP_WITHIN_SECONDS:
        flags.append({"code": "quick_hangup", "detail": f"{float(after_bot):.1f}s after Mira started speaking"})
    if longest_gap is not None and longest_gap > SLOW_RESPONSE_SECONDS:
        flags.append({"code": "slow_response", "detail": f"{longest_gap:.1f}s to start answering"})

    return {
        "guard_decisions": dict(guard_decisions or {}),
        "inaudible_turns": inaudible,
        "repeated_question_repeats": repeats,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "tool_no_results": tool_no_results,
        "longest_response_gap_s": round(longest_gap, 2) if longest_gap is not None else None,
        "ended_by": ended_by,
        "seconds_from_last_bot_start_to_end": after_bot,
        "outcome": outcome or {},
        "flagged": bool(flags),
        "flags": flags,
    }


async def load_outcome(db: AsyncSession, call_session_id: uuid.UUID) -> dict:
    """What the call led to: a lead, a booking, an escalation. Fails soft: an unknown part is simply absent."""
    from app.models.booking import Booking
    from app.models.call_session import CallSession
    from app.services import lead_service

    outcome: dict = {}
    session = await db.get(CallSession, call_session_id)
    if session is not None:
        outcome["lead"] = session.lead_id is not None
        outcome["call_type"] = session.call_type
    outcome["booking"] = (
        await db.scalar(
            select(Booking.id).where(Booking.mira_attribution_call_session_id == call_session_id).limit(1)
        )
    ) is not None
    outcome["escalation"] = await lead_service.was_escalated_during_call(db, call_session_id)
    return outcome


def top_reason(scorecards: list[dict]) -> tuple[str, int] | None:
    """The most common flag code across flagged scorecards, with its count, or None."""
    counts = Counter(flag["code"] for card in scorecards for flag in card.get("flags", []))
    if not counts:
        return None
    code, count = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return code, count
