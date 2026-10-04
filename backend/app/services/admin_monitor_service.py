"""Read-only aggregation behind the internal /admin panel (app/api/v1/admin.py).

Cross-host by design (operators, not hosts, read this), so nothing here is
ever reachable from host auth. Every function takes a Scope (date range +
whether to include browser test calls) and returns plain JSON-able dicts.

Sources, all existing tables -- nothing is read back into a live call:
- call_sessions / leads / notifications / users / properties: the product
  view (funnel, outcomes, escalations, recovery, per host/property).
- call_quality_events with an INTERNAL rule: per-call audio telemetry
  (app/voice/audio_input_observer.py) and usage/performance telemetry
  (app/voice/call_metrics.py). Only calls handled after those shipped carry
  them -- every payload reports its own `coverage` so a thin sample is
  visible instead of silently looking like zero.
- call_quality_events with a guard rule: guard/validator firings.
- service_usage_events: metered usage of paid services (usage_meter.py).
- admin_service_settings: operator-entered prepaid amounts + unit prices.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations import billing_clients
from app.models.admin import AdminServiceSetting
from app.models.call_quality_event import (
    AUDIO_INPUT_TELEMETRY_RULE,
    CALL_METRICS_RULE,
    INTERNAL_TELEMETRY_RULES,
    CallQualityEvent,
)
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.property import Property
from app.models.service_usage_event import ServiceUsageEvent
from app.models.user import User
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

QUALIFIED_CALL_TYPES = frozenset({"BOOKING_LEAD", "GUEST_SUPPORT", "EXISTING_BOOKING", "GENERAL_QUERY"})
MISSED_CALL_TYPES = frozenset({"MISSED_AGENT_BUSY", "MISSED_SYSTEM_FAILURE"})
# Segment-level audio stats come from the most recent N telemetry rows in
# range (each row carries up to 150 segments) -- bounded memory on wide
# ranges; the summary-level counters always cover every call.
AUDIO_SEGMENT_SAMPLE_CALLS = 300
MIN_CALLS_FOR_DECISION = 30
RESEND_FREE_MONTHLY = 3000
RESEND_FREE_DAILY = 100

# Billing accounts -> which metered services roll into them, and DEFAULT unit
# prices (price per ONE unit, in the account's currency). These defaults are
# starting estimates only -- every value is editable from the admin Settings
# page and should be checked against the provider's own invoice. Keys may be
# "<unit>" or "<model>:<unit>" (a model-specific override).
ACCOUNTS: dict[str, dict] = {
    "sarvam": {
        "label": "Sarvam AI (STT + TTS)",
        "kind": "prepaid",
        "currency": "INR",
        "services": ["sarvam_stt", "sarvam_tts"],
        "unit_prices": {"audio_seconds": 30.0 / 3600, "characters": 30.0 / 10000},
    },
    "exotel": {
        "label": "Exotel telephony",
        "kind": "prepaid",
        "currency": "INR",
        "services": ["exotel"],
        "unit_prices": {"minutes": 1.0},
    },
    "groq": {
        "label": "Groq (live-call LLM)",
        "kind": "postpaid",
        "currency": "USD",
        "services": ["groq"],
        "unit_prices": {"prompt_tokens": 0.15 / 1e6, "completion_tokens": 0.75 / 1e6},
    },
    "openrouter": {
        "label": "OpenRouter (fallback LLM + embeddings)",
        "kind": "live",
        "currency": "USD",
        "services": ["openrouter"],
        "unit_prices": {"prompt_tokens": 2.0 / 1e6, "completion_tokens": 8.0 / 1e6},
    },
    "anthropic": {
        "label": "Anthropic",
        "kind": "postpaid",
        "currency": "USD",
        "services": ["anthropic"],
        "unit_prices": {"prompt_tokens": 3.0 / 1e6, "completion_tokens": 15.0 / 1e6},
    },
    "twilio": {
        "label": "Twilio (WhatsApp + Voice)",
        "kind": "live",
        "currency": "USD",
        "services": ["twilio_whatsapp", "twilio_voice"],
        "unit_prices": {"messages": 0.005, "minutes": 0.0085},
    },
    "resend": {"label": "Resend (email)", "kind": "quota", "currency": "USD", "services": ["resend"], "unit_prices": {}},
    "searchapi": {
        "label": "SearchApi.io",
        "kind": "live",
        "currency": "USD",
        "services": ["searchapi"],
        "unit_prices": {},
    },
    "brightdata": {
        "label": "Bright Data",
        "kind": "live",
        "currency": "USD",
        "services": ["brightdata"],
        "unit_prices": {},
    },
    "cloudinary": {"label": "Cloudinary", "kind": "live", "currency": "USD", "services": [], "unit_prices": {}},
    "neon": {"label": "Neon Postgres", "kind": "live", "currency": "USD", "services": [], "unit_prices": {}},
}
FX_ACCOUNT = "fx"
DEFAULT_USD_INR = 88.0
SERVICE_TO_ACCOUNT = {service: account for account, cfg in ACCOUNTS.items() for service in cfg["services"]}


@dataclass(frozen=True)
class Scope:
    since: datetime
    until: datetime
    include_test_calls: bool = False


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))], 3)


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _channel(call: dict) -> str:
    if call["caller_number"] == BROWSER_TEST_CALLER_NUMBER:
        return "browser"
    return "exotel" if call["exotel_call_id"] else "twilio"


def _duration(call: dict) -> float | None:
    if call["started_at"] and call["ended_at"]:
        return max(0.0, (call["ended_at"] - call["started_at"]).total_seconds())
    return None


def _billed_minutes(call: dict) -> int:
    """Telephony bills per started minute of a connected call."""
    duration = _duration(call)
    minutes = math.ceil(duration / 60) if duration else 0
    # A busy-rejected call is still connected (placeholder audio, then
    # hangup) and billed at least one pulse, even when it was recorded with
    # no duration at all.
    if call["call_type"] == "MISSED_AGENT_BUSY":
        return max(1, minutes)
    return minutes


def _call_filters(scope: Scope) -> list:
    filters = [CallSession.created_at >= scope.since, CallSession.created_at < scope.until]
    if not scope.include_test_calls:
        filters.append(CallSession.caller_number.is_distinct_from(BROWSER_TEST_CALLER_NUMBER))
    return filters


def _histogram(values: list[float], edges: list[float]) -> list[dict]:
    buckets = [{"from": edges[i], "to": edges[i + 1], "count": 0} for i in range(len(edges) - 1)]
    for value in values:
        for bucket in buckets:
            if bucket["from"] <= value < bucket["to"] or (bucket is buckets[-1] and value == bucket["to"]):
                bucket["count"] += 1
                break
    return buckets


# --------------------------------------------------------------------------
# shared loaders
# --------------------------------------------------------------------------


async def _load_calls(db: AsyncSession, scope: Scope) -> list[dict]:
    rows = (
        await db.execute(
            select(
                CallSession.id,
                CallSession.user_id,
                CallSession.property_id,
                CallSession.caller_number,
                CallSession.exotel_call_id,
                CallSession.status,
                CallSession.call_type,
                CallSession.handoff_status,
                CallSession.started_at,
                CallSession.ended_at,
                CallSession.created_at,
                CallSession.lead_id,
            ).where(*_call_filters(scope))
        )
    ).all()
    return [dict(row._mapping) for row in rows]


async def _load_summaries(db: AsyncSession, scope: Scope, rule: str) -> dict[uuid.UUID, dict]:
    rows = (
        await db.execute(
            select(CallQualityEvent.call_session_id, CallQualityEvent.metadata_json["summary"])
            .join(CallSession, CallQualityEvent.call_session_id == CallSession.id)
            .where(*_call_filters(scope), CallQualityEvent.rule == rule)
        )
    ).all()
    return {row[0]: (row[1] or {}) for row in rows}


async def _load_leads_for_calls(db: AsyncSession, scope: Scope) -> dict[uuid.UUID, dict]:
    rows = (
        await db.execute(
            select(
                Lead.id,
                Lead.call_session_id,
                Lead.status,
                Lead.escalated,
                Lead.transferred_to_host,
                Lead.guest_name,
                Lead.phone,
                Lead.recovery_reason,
                Lead.busy_recovery_availability_status,
            )
            .join(CallSession, Lead.call_session_id == CallSession.id)
            .where(*_call_filters(scope))
        )
    ).all()
    return {row.call_session_id: dict(row._mapping) for row in rows}


async def _escalation_notifications(db: AsyncSession, scope: Scope) -> list[dict]:
    filters = [
        Notification.channel == "escalation",
        Notification.created_at >= scope.since,
        Notification.created_at < scope.until,
    ]
    stmt = select(
        Notification.call_session_id,
        Notification.urgency,
        Notification.status,
        Notification.created_at,
        Notification.responded_at,
    ).outerjoin(CallSession, Notification.call_session_id == CallSession.id)
    if not scope.include_test_calls:
        filters.append(
            or_(Notification.call_session_id.is_(None), CallSession.caller_number.is_distinct_from(BROWSER_TEST_CALLER_NUMBER))
        )
    return [dict(row._mapping) for row in (await db.execute(stmt.where(*filters))).all()]


# --------------------------------------------------------------------------
# Section A -- audio shadow week
# --------------------------------------------------------------------------


def _decision(key: str, label: str, verdict: str, metric: str, value, rule: str) -> dict:
    return {"key": key, "label": label, "verdict": verdict, "metric": metric, "value": value, "rule": rule}


async def audio(db: AsyncSession, scope: Scope) -> dict:
    calls = await _load_calls(db, scope)
    answered = [c for c in calls if c["call_type"] not in MISSED_CALL_TYPES]
    summaries = await _load_summaries(db, scope, AUDIO_INPUT_TELEMETRY_RULE)
    n = len(summaries)

    totals: Counter = Counter()
    snr, noise, overhead = [], [], []
    calls_with_vad_events = 0
    for s in summaries.values():
        for key, value in s.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] += value
        if s.get("est_snr_db") is not None:
            snr.append(s["est_snr_db"])
        if s.get("noise_floor_dbfs") is not None:
            noise.append(s["noise_floor_dbfs"])
        if s.get("observer_overhead_ms") is not None:
            overhead.append(s["observer_overhead_ms"])
        if s.get("sarvam_vad_events"):
            calls_with_vad_events += 1

    # Segment-level sample (most recent calls in range).
    sample_rows = (
        await db.execute(
            select(CallQualityEvent.metadata_json["segments"], CallQualityEvent.metadata_json["barge_ins"])
            .join(CallSession, CallQualityEvent.call_session_id == CallSession.id)
            .where(*_call_filters(scope), CallQualityEvent.rule == AUDIO_INPUT_TELEMETRY_RULE)
            .order_by(CallQualityEvent.created_at.desc())
            .limit(AUDIO_SEGMENT_SAMPLE_CALLS)
        )
    ).all()
    lang_probs, relative_db, stt_latency = [], [], []
    short_texts: dict[str, Counter] = defaultdict(Counter)
    phantom_by_source: Counter = Counter()
    sample_segments = 0
    for segments, barge_ins in sample_rows:
        for seg in segments or []:
            sample_segments += 1
            if seg.get("lang_prob") is not None:
                lang_probs.append(seg["lang_prob"])
            if seg.get("relative_db") is not None:
                relative_db.append(seg["relative_db"])
            if seg.get("stt_latency_s") is not None:
                stt_latency.append(seg["stt_latency_s"])
            if seg.get("short_text"):
                text = str(seg["short_text"]).strip().lower()
                bucket = short_texts[text]
                bucket["count"] += 1
                bucket[f"class:{seg.get('token_class')}"] += 1
                if seg.get("bot_overlap"):
                    bucket["while_bot_speaking"] += 1
                if seg.get("guard_would_trigger"):
                    bucket["guard_triggered"] += 1
        for barge in barge_ins or []:
            if barge.get("outcome") != "transcript":
                phantom_by_source[barge.get("source")] += 1

    top_short = sorted(short_texts.items(), key=lambda kv: kv[1]["count"], reverse=True)[:20]
    segments_total = totals["segments_total"]
    barge_ins = totals["barge_ins_total"]
    phantom = totals["phantom_barge_ins"]
    guard = totals["guard_triggers"]
    overlap = totals["segments_bot_overlap"]
    enough = n >= MIN_CALLS_FOR_DECISION

    def verdict(condition: bool) -> str:
        if not enough:
            return "insufficient_data"
        return "enable" if condition else "hold"

    phantom_rate = _rate(phantom, barge_ins)
    sarvam_share = _rate(calls_with_vad_events, n)
    echo_per_100 = round(100 * totals["shadow_echo_drop"] / segments_total, 2) if segments_total else None
    short_rate = _rate(totals["shadow_short_drop"], overlap)
    short_guard_share = _rate(totals["guard_triggers_short"], guard)
    decisions = [
        _decision(
            "1.5",
            "Stop Sarvam's server VAD from interrupting (vad_signals=False)",
            "insufficient_data" if not enough else ("enable" if (sarvam_share or 0) > 0 and phantom_by_source.get("sarvam_stt", 0) > phantom_by_source.get("downstream_turn_controller", 0) else "hold"),
            "calls where Sarvam sent VAD events",
            sarvam_share,
            "Enable if Sarvam's VAD is active AND causes more phantom barge-ins than local VAD.",
        ),
        _decision(
            "1.6",
            "Stricter barge-in while Mira speaks (bot-aware start + 2 words)",
            verdict((phantom_rate or 0) > 0.2),
            "phantom barge-in rate",
            phantom_rate,
            "Enable if more than 20% of barge-ins never turn into real words.",
        ),
        _decision(
            "1.7",
            "Drop echoes of Mira's own speech",
            verdict((echo_per_100 or 0) >= 1),
            "echo matches per 100 segments",
            echo_per_100,
            "Enable at >= 1 per 100 segments, after spot-checking flagged calls' transcripts.",
        ),
        _decision(
            "1.8",
            "Ignore 1-word fragments while Mira speaks (stop-word allow-list)",
            verdict((short_rate or 0) >= 0.05),
            "share of overlapping segments that are 1-word non-stop-words",
            short_rate,
            "Enable at >= 5%; add any frequent 'number' token to the allow-list first.",
        ),
        _decision(
            "guard-short",
            "Exempt short replies ('okay', 'haan') from the low-confidence guard",
            verdict((short_guard_share or 0) >= 0.5),
            "share of guard triggers on <= 2-word replies",
            short_guard_share,
            "Enable if most 'say that again' prompts fire on short replies.",
        ),
    ]

    return {
        "coverage": {
            "calls_in_range": len(calls),
            "answered_calls": len(answered),
            "calls_with_audio_telemetry": n,
            "segment_sample_calls": len(sample_rows),
            "segment_sample_size": sample_segments,
            "min_calls_for_decision": MIN_CALLS_FOR_DECISION,
        },
        "decisions": decisions,
        "interruptions": {
            "barge_ins_total": barge_ins,
            "barge_ins_per_call": _rate(barge_ins, n),
            "phantom_barge_ins": phantom,
            "phantom_rate": phantom_rate,
            "by_source": {
                "sarvam_stt": totals["barge_ins_sarvam_stt"],
                "downstream_turn_controller": totals["barge_ins_downstream_turn_controller"],
            },
            "phantom_by_source_sample": dict(phantom_by_source),
            "dual_source": totals["barge_ins_dual_source"],
            "transcript_triggered": totals["barge_ins_transcript_triggered"],
            "outcomes": {
                key.removeprefix("barge_in_outcome_"): value
                for key, value in totals.items()
                if key.startswith("barge_in_outcome_")
            },
            "calls_with_sarvam_vad_events": calls_with_vad_events,
            "sarvam_vad_share": sarvam_share,
        },
        "segments": {
            "total": segments_total,
            "short": totals["short_segments"],
            "short_share": _rate(totals["short_segments"], segments_total),
            "bot_overlap": overlap,
            "bot_overlap_share": _rate(overlap, segments_total),
        },
        "shadow": {
            "echo_drop": totals["shadow_echo_drop"],
            "echo_per_100_segments": echo_per_100,
            "short_drop": totals["shadow_short_drop"],
            "short_drop_by_class": {
                cls: totals[f"shadow_short_drop_{cls}"] for cls in ("backchannel", "number", "other")
            },
            "level_drop": totals["shadow_level_drop"],
            "level_clarify": totals["shadow_level_clarify"],
            "any_action_share": _rate(totals["segments_any_shadow_action"], segments_total),
        },
        "guard": {
            "triggers": guard,
            "trigger_rate": _rate(guard, segments_total),
            "short_triggers": totals["guard_triggers_short"],
            "short_share_of_triggers": short_guard_share,
        },
        "levels": {
            "snr_db": {"p10": _pct(snr, 10), "p50": _pct(snr, 50), "p90": _pct(snr, 90)},
            "noise_floor_dbfs_p50": _pct(noise, 50),
            "lang_prob_histogram": _histogram(lang_probs, [round(i / 10, 1) for i in range(11)]),
            "relative_db_histogram": _histogram(relative_db, [-60, -30, -24, -18, -12, -6, 0, 6, 12, 60]),
        },
        "stt_latency_s": {"p50": _pct(stt_latency, 50), "p95": _pct(stt_latency, 95)},
        "top_short_texts": [
            {
                "text": text,
                "count": c["count"],
                "token_class": next((k.split(":", 1)[1] for k in c if k.startswith("class:")), None),
                "while_bot_speaking": c["while_bot_speaking"],
                "guard_triggered": c["guard_triggered"],
            }
            for text, c in top_short
        ],
        "overhead_ms": {"p50": _pct(overhead, 50), "max": max(overhead) if overhead else None},
    }


# --------------------------------------------------------------------------
# Section B -- agent performance (1-10)
# --------------------------------------------------------------------------


def _end_label(summary: dict | None, call: dict) -> str:
    if summary is None:
        if call["call_type"] == "MISSED_AGENT_BUSY":
            return "busy_rejected"
        if call["call_type"] == "MISSED_SYSTEM_FAILURE":
            return "system_failure"
        return "unknown"
    reason = summary.get("end_reason")
    if reason == "max_call_duration_exceeded":
        return "max_duration"
    if reason == "host_handoff":
        return "host_handoff"
    if reason == "silent caller":
        return "silent_caller"
    if summary.get("end_frame") == "CancelFrame":
        return "guest_hung_up"
    return "mira_ended"


async def conversations(db: AsyncSession, scope: Scope) -> dict:
    """B1 funnel, B2 outcomes, B3 understanding, B8 language."""
    calls = await _load_calls(db, scope)
    metrics = await _load_summaries(db, scope, CALL_METRICS_RULE)
    audio_summaries = await _load_summaries(db, scope, AUDIO_INPUT_TELEMETRY_RULE)
    leads = await _load_leads_for_calls(db, scope)
    escalations = await _escalation_notifications(db, scope)
    escalated_calls = {e["call_session_id"] for e in escalations if e["call_session_id"]}

    answered = [c for c in calls if c["call_type"] not in MISSED_CALL_TYPES]

    def engaged(call: dict) -> bool:
        m = metrics.get(call["id"])
        if m is not None:
            return (m.get("user_turns") or 0) >= 2
        return call["call_type"] in QUALIFIED_CALL_TYPES

    engaged_calls = [c for c in answered if engaged(c)]
    with_lead = [c for c in engaged_calls if c["id"] in leads or c["lead_id"]]
    telemetry_engaged = [c for c in engaged_calls if c["id"] in metrics]
    price_quoted = [c for c in telemetry_engaged if metrics[c["id"]].get("price_quoted")]
    negotiated = [c for c in telemetry_engaged if metrics[c["id"]].get("negotiated")]

    def lead_of(call):
        return leads.get(call["id"]) or {}

    escalated = [c for c in answered if c["id"] in escalated_calls or lead_of(c).get("escalated")]
    transferred = [
        c for c in answered if lead_of(c).get("transferred_to_host") or c["handoff_status"] in ("requested", "connected")
    ]
    booked = [c for c in answered if lead_of(c).get("status") == "booked"]

    funnel = [
        {"key": "calls", "label": "Calls received", "count": len(calls)},
        {"key": "answered", "label": "Answered by Mira", "count": len(answered)},
        {"key": "engaged", "label": "Engaged (2+ guest turns)", "count": len(engaged_calls)},
        {"key": "lead", "label": "Lead captured", "count": len(with_lead)},
        {
            "key": "price_quoted",
            "label": "Price quoted",
            "count": len(price_quoted),
            "base": len(telemetry_engaged),
            "telemetry_only": True,
        },
        {
            "key": "negotiated",
            "label": "Negotiated",
            "count": len(negotiated),
            "base": len(telemetry_engaged),
            "telemetry_only": True,
        },
        {"key": "booked", "label": "Booked (host-marked)", "count": len(booked)},
    ]
    previous = None
    for step in funnel:
        base = step.get("base", previous)
        step["rate_of_previous"] = _rate(step["count"], base) if base is not None else None
        step["rate_of_calls"] = _rate(step["count"], len(calls))
        if not step.get("telemetry_only"):
            previous = step["count"]

    durations = [d for c in answered if (d := _duration(c)) is not None]
    end_labels = Counter(_end_label(metrics.get(c["id"]), c) for c in calls)
    guard_rows = (
        await db.execute(
            select(CallQualityEvent.rule, func.count())
            .join(CallSession, CallQualityEvent.call_session_id == CallSession.id)
            .where(*_call_filters(scope), CallQualityEvent.rule.not_in(INTERNAL_TELEMETRY_RULES))
            .group_by(CallQualityEvent.rule)
            .order_by(func.count().desc())
        )
    ).all()

    clarifications = [s.get("guard_triggers", 0) for s in audio_summaries.values()]
    lang_totals: Counter = Counter()
    dominant: Counter = Counter()
    switches = []
    for s in audio_summaries.values():
        counts = s.get("lang_counts") or {}
        lang_totals.update(counts)
        if counts:
            dominant[max(counts.items(), key=lambda kv: kv[1])[0]] += 1
        switches.append(s.get("lang_switches", 0))

    return {
        "coverage": {
            "calls": len(calls),
            "calls_with_call_metrics": len(metrics),
            "calls_with_audio_telemetry": len(audio_summaries),
        },
        "funnel": funnel,
        "outcomes": {
            "escalated": len(escalated),
            "transferred_to_host": len(transferred),
            "by_call_type": [{"call_type": k, "count": v} for k, v in Counter(c["call_type"] for c in calls).most_common()],
            "by_end": [{"end": k, "count": v} for k, v in end_labels.most_common()],
            "duration_s": {
                "avg": round(sum(durations) / len(durations), 1) if durations else None,
                "p50": _pct(durations, 50),
                "p90": _pct(durations, 90),
            },
            "short_calls_under_20s": sum(1 for d in durations if d < 20),
            "short_call_share": _rate(sum(1 for d in durations if d < 20), len(durations)),
        },
        "understanding": {
            "guard_firings": [
                {"rule": rule, "count": count, "per_100_calls": round(100 * count / len(answered), 1) if answered else None}
                for rule, count in guard_rows
            ],
            "clarifications_total": sum(clarifications),
            "clarifications_per_call": _rate(sum(clarifications), len(clarifications)),
            "calls_with_clarification_share": _rate(sum(1 for c in clarifications if c), len(clarifications)),
        },
        "language": {
            "segments_by_language": [{"language": k, "segments": v} for k, v in lang_totals.most_common()],
            "calls_by_dominant_language": [{"language": k, "calls": v} for k, v in dominant.most_common()],
            "avg_switches_per_call": _rate(sum(switches), len(switches)),
            "calls_with_switch_share": _rate(sum(1 for s in switches if s), len(switches)),
        },
    }


async def performance(db: AsyncSession, scope: Scope) -> dict:
    """B4 speed, B5 LLM/tool reliability."""
    calls = await _load_calls(db, scope)
    metrics = await _load_summaries(db, scope, CALL_METRICS_RULE)

    latencies, llm_ttfb, tts_ttfb = [], [], []
    tool_calls: Counter = Counter()
    tool_errors: Counter = Counter()
    tool_durations: dict[str, list[float]] = defaultdict(list)
    models: dict[tuple, Counter] = defaultdict(Counter)
    errors_total = 0
    calls_with_errors = 0
    for m in metrics.values():
        latencies.extend(m.get("response_latency_s") or [])
        ttfb = m.get("ttfb_s") or {}
        llm_ttfb.extend(ttfb.get("llm") or [])
        tts_ttfb.extend(ttfb.get("tts") or [])
        tool_calls.update(m.get("tool_calls") or {})
        tool_errors.update(m.get("tool_errors") or {})
        for name, values in (m.get("tool_durations_s") or {}).items():
            tool_durations[name].extend(values)
        for entry in m.get("llm") or []:
            bucket = models[(entry.get("provider"), entry.get("model"))]
            for key in ("completions", "prompt_tokens", "completion_tokens"):
                bucket[key] += entry.get(key) or 0
        errors_total += m.get("errors") or 0
        calls_with_errors += 1 if m.get("errors") else 0

    total_completions = sum(c["completions"] for c in models.values())
    primary = settings.groq_model
    fallback_completions = sum(c["completions"] for (provider, model), c in models.items() if model != primary)

    return {
        "coverage": {"calls": len(calls), "calls_with_call_metrics": len(metrics)},
        "latency": {
            "response_s": {
                "p50": _pct(latencies, 50),
                "p90": _pct(latencies, 90),
                "p95": _pct(latencies, 95),
                "samples": len(latencies),
            },
            "turn_detection_wait_s": 0.9,
            "llm_ttfb_s": {"p50": _pct(llm_ttfb, 50), "p95": _pct(llm_ttfb, 95)},
            "tts_ttfb_s": {"p50": _pct(tts_ttfb, 50), "p95": _pct(tts_ttfb, 95)},
            "response_histogram": _histogram(latencies, [0, 0.5, 1, 1.5, 2, 3, 4, 6, 30]),
        },
        "tools": sorted(
            [
                {
                    "name": name,
                    "calls": count,
                    "errors": tool_errors.get(name, 0),
                    "error_rate": _rate(tool_errors.get(name, 0), count),
                    "p50_s": _pct(tool_durations.get(name, []), 50),
                    "p95_s": _pct(tool_durations.get(name, []), 95),
                }
                for name, count in tool_calls.items()
            ],
            key=lambda t: t["calls"],
            reverse=True,
        ),
        "llm": {
            "models": sorted(
                [
                    {
                        "provider": provider,
                        "model": model,
                        "completions": c["completions"],
                        "prompt_tokens": c["prompt_tokens"],
                        "completion_tokens": c["completion_tokens"],
                        "share": _rate(c["completions"], total_completions),
                    }
                    for (provider, model), c in models.items()
                ],
                key=lambda m: m["completions"],
                reverse=True,
            ),
            "primary_model": primary,
            "fallback_share": _rate(fallback_completions, total_completions),
            "pipeline_errors": errors_total,
            "calls_with_errors_share": _rate(calls_with_errors, len(metrics)),
            "system_failures": sum(1 for c in calls if c["call_type"] == "MISSED_SYSTEM_FAILURE"),
        },
    }


async def leads_and_recovery(db: AsyncSession, scope: Scope) -> dict:
    """B6 lead safety, B7 busy recovery, B9 escalations."""
    calls = await _load_calls(db, scope)
    by_id = {c["id"]: c for c in calls}
    metrics = await _load_summaries(db, scope, CALL_METRICS_RULE)
    leads = await _load_leads_for_calls(db, scope)

    engaged_pricing = [cid for cid, m in metrics.items() if m.get("engaged_pricing_tools")]
    violations = [cid for cid in engaged_pricing if cid not in leads and not by_id.get(cid, {}).get("lead_id")]
    via_update_lead = [cid for cid in leads if cid in metrics and (metrics[cid].get("tool_calls") or {}).get("update_lead")]
    via_safety_net = [cid for cid in leads if cid in metrics and not (metrics[cid].get("tool_calls") or {}).get("update_lead")]
    incomplete = [l for l in leads.values() if not l.get("phone") and not l.get("guest_name")]

    busy_calls = [c for c in calls if c["call_type"] == "MISSED_AGENT_BUSY"]
    recovery_leads = [l for l in leads.values() if l.get("recovery_reason")]
    availability = Counter(l.get("busy_recovery_availability_status") or "none" for l in recovery_leads)
    recovered_progressed = [l for l in recovery_leads if l.get("status") in ("contacted", "booked")]

    escalations = await _escalation_notifications(db, scope)
    response_minutes = [
        (e["responded_at"] - e["created_at"]).total_seconds() / 60
        for e in escalations
        if e["responded_at"] and e["created_at"]
    ]
    now = datetime.now(timezone.utc)
    stale = [e for e in escalations if not e["responded_at"] and e["created_at"] and now - e["created_at"] > timedelta(hours=1)]

    return {
        "coverage": {"calls": len(calls), "calls_with_call_metrics": len(metrics)},
        "lead_safety": {
            "leads_from_calls": len(leads),
            "calls_with_pricing_engagement": len(engaged_pricing),
            "engaged_without_lead": len(violations),
            "engaged_without_lead_call_ids": [str(cid) for cid in violations[:20]],
            "leads_via_update_lead": len(via_update_lead),
            "leads_via_safety_net": len(via_safety_net),
            "safety_net_share": _rate(len(via_safety_net), len(via_update_lead) + len(via_safety_net)),
            "incomplete_leads": len(incomplete),
        },
        "busy_recovery": {
            "busy_rejections": len(busy_calls),
            "recovery_leads": len(recovery_leads),
            "recovery_rate": _rate(len(recovery_leads), len(busy_calls)),
            "availability_status": [{"status": k, "count": v} for k, v in availability.most_common()],
            "progressed": len(recovered_progressed),
        },
        "escalations": {
            "total": len(escalations),
            "by_urgency": [{"urgency": k, "count": v} for k, v in Counter(e["urgency"] for e in escalations).most_common()],
            "by_status": [{"status": k, "count": v} for k, v in Counter(e["status"] for e in escalations).most_common()],
            "responded": len(response_minutes),
            "response_minutes": {"p50": _pct(response_minutes, 50), "p90": _pct(response_minutes, 90)},
            "unresponded_over_1h": len(stale),
        },
    }


async def hosts(db: AsyncSession, scope: Scope) -> dict:
    """B10 per host / per property."""
    calls = await _load_calls(db, scope)
    metrics = await _load_summaries(db, scope, CALL_METRICS_RULE)
    leads = await _load_leads_for_calls(db, scope)
    escalations = await _escalation_notifications(db, scope)
    escalated_calls = {e["call_session_id"] for e in escalations if e["call_session_id"]}
    guard_by_call = dict(
        (
            await db.execute(
                select(CallQualityEvent.call_session_id, func.count())
                .join(CallSession, CallQualityEvent.call_session_id == CallSession.id)
                .where(*_call_filters(scope), CallQualityEvent.rule.not_in(INTERNAL_TELEMETRY_RULES))
                .group_by(CallQualityEvent.call_session_id)
            )
        ).all()
    )
    cost_by_user = await _cost_by_user(db, scope)

    user_ids = {c["user_id"] for c in calls if c["user_id"]}
    property_ids = {c["property_id"] for c in calls if c["property_id"]}
    users = {
        row.id: row
        for row in (await db.execute(select(User.id, User.email, User.name, User.business_name).where(User.id.in_(user_ids)))).all()
    } if user_ids else {}
    properties = {
        row.id: row
        for row in (await db.execute(select(Property.id, Property.name, Property.city).where(Property.id.in_(property_ids)))).all()
    } if property_ids else {}

    def aggregate(group_calls: list[dict]) -> dict:
        answered = [c for c in group_calls if c["call_type"] not in MISSED_CALL_TYPES]
        durations = [d for c in answered if (d := _duration(c)) is not None]
        latencies = [v for c in group_calls for v in (metrics.get(c["id"], {}).get("response_latency_s") or [])]
        return {
            "calls": len(group_calls),
            "answered": len(answered),
            "busy_rejected": sum(1 for c in group_calls if c["call_type"] == "MISSED_AGENT_BUSY"),
            "leads": sum(1 for c in group_calls if c["id"] in leads or c["lead_id"]),
            "escalations": sum(1 for c in group_calls if c["id"] in escalated_calls),
            "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
            "guard_firings_per_call": _rate(sum(guard_by_call.get(c["id"], 0) for c in answered), len(answered)),
            "response_p50_s": _pct(latencies, 50),
            "last_call_at": max((c["created_at"] for c in group_calls), default=None),
        }

    by_user: dict = defaultdict(list)
    by_property: dict = defaultdict(list)
    for c in calls:
        by_user[c["user_id"]].append(c)
        if c["property_id"]:
            by_property[c["property_id"]].append(c)

    host_rows = []
    for uid, group in by_user.items():
        u = users.get(uid)
        row = aggregate(group)
        row.update(
            {
                "user_id": str(uid) if uid else None,
                "name": (u.business_name or u.name or u.email) if u else "Unknown host",
                "email": u.email if u else None,
                "cost_inr": round(cost_by_user.get(uid, 0.0), 2),
                "cost_per_answered_inr": round(cost_by_user.get(uid, 0.0) / row["answered"], 2) if row["answered"] else None,
            }
        )
        host_rows.append(row)
    property_rows = []
    for pid, group in by_property.items():
        p = properties.get(pid)
        row = aggregate(group)
        row.update({"property_id": str(pid), "name": p.name if p else "Unknown property", "city": p.city if p else None})
        property_rows.append(row)

    host_rows.sort(key=lambda r: r["calls"], reverse=True)
    property_rows.sort(key=lambda r: r["calls"], reverse=True)
    return {"hosts": host_rows, "properties": property_rows[:50]}


# --------------------------------------------------------------------------
# Section C -- usage, cost, balances
# --------------------------------------------------------------------------


async def _settings_map(db: AsyncSession) -> dict[str, AdminServiceSetting]:
    return {row.service: row for row in (await db.scalars(select(AdminServiceSetting))).all()}


def _effective_prices(account: str, stored: AdminServiceSetting | None) -> dict:
    prices = dict(ACCOUNTS.get(account, {}).get("unit_prices", {}))
    if stored is not None and stored.unit_prices:
        prices.update({k: float(v) for k, v in stored.unit_prices.items() if v is not None})
    return prices


def _currency(account: str, stored: AdminServiceSetting | None) -> str:
    return (stored.currency if stored is not None and stored.currency else None) or ACCOUNTS[account]["currency"]


def _usd_inr(settings_map: dict) -> float:
    fx = settings_map.get(FX_ACCOUNT)
    if fx is not None and fx.unit_prices.get("usd_inr"):
        return float(fx.unit_prices["usd_inr"])
    return DEFAULT_USD_INR


def _price_for(prices: dict, unit: str, model: str | None) -> float:
    if model and f"{model}:{unit}" in prices:
        return prices[f"{model}:{unit}"]
    return prices.get(unit, 0.0)


async def _usage_rows(db: AsyncSession, since: datetime, until: datetime, services: list[str] | None = None) -> list[dict]:
    stmt = (
        select(
            ServiceUsageEvent.service,
            ServiceUsageEvent.unit,
            ServiceUsageEvent.model,
            func.sum(ServiceUsageEvent.quantity).label("quantity"),
        )
        .where(ServiceUsageEvent.created_at >= since, ServiceUsageEvent.created_at < until)
        .group_by(ServiceUsageEvent.service, ServiceUsageEvent.unit, ServiceUsageEvent.model)
    )
    if services is not None:
        stmt = stmt.where(ServiceUsageEvent.service.in_(services))
    return [dict(row._mapping) for row in (await db.execute(stmt)).all()]


async def _derived_minutes(db: AsyncSession, since: datetime, until: datetime) -> dict[str, float]:
    """Exotel/Twilio Voice minutes, derived from call durations (billed per
    started minute) -- the same data the telephony providers bill on."""
    rows = (
        await db.execute(
            select(
                CallSession.caller_number,
                CallSession.exotel_call_id,
                CallSession.call_type,
                CallSession.started_at,
                CallSession.ended_at,
            ).where(
                CallSession.created_at >= since,
                CallSession.created_at < until,
                CallSession.caller_number.is_distinct_from(BROWSER_TEST_CALLER_NUMBER),
            )
        )
    ).all()
    minutes: Counter = Counter()
    for row in rows:
        call = dict(row._mapping)
        minutes["exotel" if call["exotel_call_id"] else "twilio_voice"] += _billed_minutes(call)
    return dict(minutes)


async def _priced_usage(db: AsyncSession, since: datetime, until: datetime, settings_map: dict) -> list[dict]:
    rows = await _usage_rows(db, since, until)
    minutes = await _derived_minutes(db, since, until)
    for service, qty in minutes.items():
        if qty:
            rows.append({"service": service, "unit": "minutes", "model": None, "quantity": float(qty)})
    usd_inr = _usd_inr(settings_map)
    priced = []
    for row in rows:
        account = SERVICE_TO_ACCOUNT.get(row["service"], row["service"])
        stored = settings_map.get(account)
        prices = _effective_prices(account, stored) if account in ACCOUNTS else {}
        currency = _currency(account, stored) if account in ACCOUNTS else "USD"
        unit_price = _price_for(prices, row["unit"], row["model"])
        cost = float(row["quantity"]) * unit_price
        priced.append(
            {
                "account": account,
                "service": row["service"],
                "unit": row["unit"],
                "model": row["model"],
                "quantity": round(float(row["quantity"]), 3),
                "unit_price": unit_price,
                "currency": currency,
                "cost": round(cost, 4),
                "cost_inr": round(cost * (usd_inr if currency == "USD" else 1.0), 2),
                "priced": unit_price > 0,
            }
        )
    return priced


async def _cost_by_user(db: AsyncSession, scope: Scope) -> dict:
    settings_map = await _settings_map(db)
    usd_inr = _usd_inr(settings_map)
    rows = (
        await db.execute(
            select(
                ServiceUsageEvent.user_id,
                ServiceUsageEvent.service,
                ServiceUsageEvent.unit,
                ServiceUsageEvent.model,
                func.sum(ServiceUsageEvent.quantity),
            )
            .where(
                ServiceUsageEvent.created_at >= scope.since,
                ServiceUsageEvent.created_at < scope.until,
                ServiceUsageEvent.user_id.is_not(None),
            )
            .group_by(ServiceUsageEvent.user_id, ServiceUsageEvent.service, ServiceUsageEvent.unit, ServiceUsageEvent.model)
        )
    ).all()
    costs: Counter = Counter()
    for user_id, service, unit, model, quantity in rows:
        account = SERVICE_TO_ACCOUNT.get(service)
        if account is None:
            continue
        stored = settings_map.get(account)
        cost = float(quantity) * _price_for(_effective_prices(account, stored), unit, model)
        costs[user_id] += cost * (usd_inr if _currency(account, stored) == "USD" else 1.0)
    # Telephony minutes per host, derived from that host's call durations.
    calls = (
        await db.execute(
            select(
                CallSession.user_id,
                CallSession.caller_number,
                CallSession.exotel_call_id,
                CallSession.call_type,
                CallSession.started_at,
                CallSession.ended_at,
            ).where(
                CallSession.created_at >= scope.since,
                CallSession.created_at < scope.until,
                CallSession.caller_number.is_distinct_from(BROWSER_TEST_CALLER_NUMBER),
            )
        )
    ).all()
    for row in calls:
        call = dict(row._mapping)
        account = "exotel" if call["exotel_call_id"] else "twilio"
        stored = settings_map.get(account)
        price = _price_for(_effective_prices(account, stored), "minutes", None)
        costs[call["user_id"]] += _billed_minutes(call) * price * (usd_inr if _currency(account, stored) == "USD" else 1.0)
    return dict(costs)


async def _daily_cost_inr(db: AsyncSession, scope: Scope, settings_map: dict) -> list[dict]:
    """Daily cost trend (INR) in two queries total: metered usage grouped by
    UTC day, plus telephony minutes from that day's calls."""
    usd_inr = _usd_inr(settings_map)

    def inr(account: str, unit: str, model: str | None, quantity: float) -> float:
        if account not in ACCOUNTS:
            return 0.0
        stored = settings_map.get(account)
        cost = quantity * _price_for(_effective_prices(account, stored), unit, model)
        return cost * (usd_inr if _currency(account, stored) == "USD" else 1.0)

    day_col = func.date_trunc("day", func.timezone("UTC", ServiceUsageEvent.created_at))
    totals: Counter = Counter()
    for day, service, unit, model, quantity in (
        await db.execute(
            select(day_col, ServiceUsageEvent.service, ServiceUsageEvent.unit, ServiceUsageEvent.model, func.sum(ServiceUsageEvent.quantity))
            .where(ServiceUsageEvent.created_at >= scope.since, ServiceUsageEvent.created_at < scope.until)
            .group_by(day_col, ServiceUsageEvent.service, ServiceUsageEvent.unit, ServiceUsageEvent.model)
        )
    ).all():
        totals[day.date().isoformat()] += inr(SERVICE_TO_ACCOUNT.get(service, service), unit, model, float(quantity))
    for row in (
        await db.execute(
            select(
                CallSession.created_at,
                CallSession.caller_number,
                CallSession.exotel_call_id,
                CallSession.call_type,
                CallSession.started_at,
                CallSession.ended_at,
            ).where(
                CallSession.created_at >= scope.since,
                CallSession.created_at < scope.until,
                CallSession.caller_number.is_distinct_from(BROWSER_TEST_CALLER_NUMBER),
            )
        )
    ).all():
        call = dict(row._mapping)
        account = "exotel" if call["exotel_call_id"] else "twilio"
        day = call["created_at"].astimezone(timezone.utc).date().isoformat()
        totals[day] += inr(account, "minutes", None, _billed_minutes(call))

    days = []
    day = scope.since
    while day < scope.until and len(days) < 400:
        key = day.date().isoformat()
        days.append({"date": key, "cost_inr": round(totals.get(key, 0.0), 2)})
        day += timedelta(days=1)
    return days


async def usage(db: AsyncSession, scope: Scope) -> dict:
    settings_map = await _settings_map(db)
    priced = await _priced_usage(db, scope.since, scope.until, settings_map)
    calls = await _load_calls(db, scope)
    answered = [c for c in calls if c["call_type"] not in MISSED_CALL_TYPES and _channel(c) != "browser"]

    by_account: dict[str, dict] = {}
    for row in priced:
        acc = by_account.setdefault(
            row["account"],
            {
                "account": row["account"],
                "label": ACCOUNTS.get(row["account"], {}).get("label", row["account"]),
                "currency": row["currency"],
                "cost": 0.0,
                "cost_inr": 0.0,
                "unpriced_units": [],
            },
        )
        acc["cost"] = round(acc["cost"] + row["cost"], 4)
        acc["cost_inr"] = round(acc["cost_inr"] + row["cost_inr"], 2)
        if not row["priced"] and row["unit"] not in acc["unpriced_units"]:
            acc["unpriced_units"].append(row["unit"])
    total_inr = round(sum(a["cost_inr"] for a in by_account.values()), 2)

    days = await _daily_cost_inr(db, scope, settings_map)

    return {
        "usd_inr": _usd_inr(settings_map),
        "rows": sorted(priced, key=lambda r: r["cost_inr"], reverse=True),
        "by_account": sorted(by_account.values(), key=lambda a: a["cost_inr"], reverse=True),
        "totals": {
            "cost_inr": total_inr,
            "answered_phone_calls": len(answered),
            "cost_per_answered_call_inr": round(total_inr / len(answered), 2) if answered else None,
        },
        "daily": days,
    }


async def _spend_since(db: AsyncSession, account: str, since: datetime, settings_map: dict) -> float:
    until = datetime.now(timezone.utc) + timedelta(minutes=1)
    priced = await _priced_usage(db, since, until, settings_map)
    return round(sum(r["cost"] for r in priced if r["account"] == account), 4)


def _level(remaining_share: float | None) -> str:
    if remaining_share is None:
        return "unknown"
    if remaining_share <= 0.10:
        return "critical"
    if remaining_share <= 0.20:
        return "low"
    return "ok"


async def balances(db: AsyncSession, *, refresh: bool = False) -> list[dict]:
    settings_map = await _settings_map(db)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cards = []

    for account in ("sarvam", "exotel"):
        stored = settings_map.get(account)
        currency = _currency(account, stored)
        card = {"account": account, "label": ACCOUNTS[account]["label"], "kind": "prepaid", "currency": currency}
        last_7 = await _spend_since(db, account, now - timedelta(days=7), settings_map)
        burn = round(last_7 / 7, 2)
        if stored is None or stored.prepaid_amount is None or stored.prepaid_set_at is None:
            card.update({"status": "not_set", "burn_per_day": burn, "detail": "Enter the current balance in Settings."})
        else:
            spent = await _spend_since(db, account, stored.prepaid_set_at, settings_map)
            prepaid = float(stored.prepaid_amount)
            remaining = round(prepaid - spent, 2)
            card.update(
                {
                    "status": "ok",
                    "level": _level(_rate(remaining, prepaid)),
                    "prepaid_amount": prepaid,
                    "prepaid_set_at": stored.prepaid_set_at.isoformat(),
                    "spent_since": spent,
                    "balance": remaining,
                    "remaining_share": _rate(remaining, prepaid),
                    "burn_per_day": burn,
                    "days_left": round(remaining / burn, 1) if burn > 0 else None,
                }
            )
        cards.append(card)

    groq_stored = settings_map.get("groq")
    groq_spent = await _spend_since(db, "groq", month_start, settings_map)
    budget = float(groq_stored.prepaid_amount) if groq_stored and groq_stored.prepaid_amount is not None else None
    cards.append(
        {
            "account": "groq",
            "label": ACCOUNTS["groq"]["label"],
            "kind": "postpaid",
            "status": "ok",
            "currency": _currency("groq", groq_stored),
            "used": groq_spent,
            "limit": budget,
            "level": _level(_rate(budget - groq_spent, budget)) if budget else "unknown",
            "detail": "No balance API (postpaid). Metered spend this month vs the monthly budget set in Settings.",
        }
    )

    resend_rows = await _usage_rows(db, month_start, now + timedelta(minutes=1), ["resend"])
    resend_today = await _usage_rows(
        db, now.replace(hour=0, minute=0, second=0, microsecond=0), now + timedelta(minutes=1), ["resend"]
    )
    sent_month = sum(r["quantity"] for r in resend_rows)
    cards.append(
        {
            "account": "resend",
            "label": ACCOUNTS["resend"]["label"],
            "kind": "quota",
            "status": "ok",
            "used": sent_month,
            "limit": RESEND_FREE_MONTHLY,
            "balance": RESEND_FREE_MONTHLY - sent_month,
            "unit": "emails this month",
            "level": _level(_rate(RESEND_FREE_MONTHLY - sent_month, RESEND_FREE_MONTHLY)),
            "detail": f"{int(sum(r['quantity'] for r in resend_today))} of {RESEND_FREE_DAILY} free emails today",
        }
    )

    live = await asyncio.gather(
        *(billing_clients.fetch_balance(name, refresh=refresh) for name in billing_clients.LIVE_BALANCE_FETCHERS)
    )
    for result in live:
        account = result["service"]
        card = {"account": account, "label": ACCOUNTS[account]["label"], "kind": "live", **result}
        if result.get("limit") and result.get("balance") is not None:
            card["level"] = _level(_rate(result["balance"], result["limit"]))
        cards.append(card)
    return cards


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


async def list_service_settings(db: AsyncSession) -> list[dict]:
    settings_map = await _settings_map(db)
    out = []
    for account, cfg in ACCOUNTS.items():
        stored = settings_map.get(account)
        out.append(
            {
                "account": account,
                "label": cfg["label"],
                "kind": cfg["kind"],
                "currency": _currency(account, stored),
                "unit_prices": _effective_prices(account, stored),
                "default_unit_prices": cfg["unit_prices"],
                "prepaid_amount": float(stored.prepaid_amount) if stored and stored.prepaid_amount is not None else None,
                "prepaid_set_at": stored.prepaid_set_at.isoformat() if stored and stored.prepaid_set_at else None,
                "note": stored.note if stored else None,
                "updated_by": stored.updated_by if stored else None,
            }
        )
    out.append(
        {
            "account": FX_ACCOUNT,
            "label": "USD → INR rate",
            "kind": "fx",
            "currency": "INR",
            "unit_prices": {"usd_inr": _usd_inr(settings_map)},
            "default_unit_prices": {"usd_inr": DEFAULT_USD_INR},
            "prepaid_amount": None,
            "prepaid_set_at": None,
            "note": None,
            "updated_by": settings_map[FX_ACCOUNT].updated_by if FX_ACCOUNT in settings_map else None,
        }
    )
    return out


async def update_service_setting(
    db: AsyncSession,
    account: str,
    *,
    admin_email: str,
    prepaid_amount: float | None = None,
    clear_prepaid: bool = False,
    unit_prices: dict | None = None,
    currency: str | None = None,
    note: str | None = None,
) -> None:
    if account not in ACCOUNTS and account != FX_ACCOUNT:
        raise KeyError(account)
    stored = await db.scalar(select(AdminServiceSetting).where(AdminServiceSetting.service == account))
    if stored is None:
        stored = AdminServiceSetting(
            service=account,
            currency=ACCOUNTS[account]["currency"] if account in ACCOUNTS else "INR",
            unit_prices={},
        )
        db.add(stored)
    if clear_prepaid:
        stored.prepaid_amount = None
        stored.prepaid_set_at = None
    elif prepaid_amount is not None:
        # Entering a balance restarts the "spent since" clock: remaining is
        # always this amount minus usage metered AFTER it was entered.
        stored.prepaid_amount = prepaid_amount
        stored.prepaid_set_at = datetime.now(timezone.utc)
    if unit_prices is not None:
        stored.unit_prices = {k: float(v) for k, v in unit_prices.items() if v is not None}
    if currency is not None:
        stored.currency = currency
    if note is not None:
        stored.note = note
    stored.updated_by = admin_email
    await db.commit()


async def overview(db: AsyncSession, scope: Scope) -> dict:
    conv = await conversations(db, scope)
    perf = await performance(db, scope)
    aud = await audio(db, scope)
    use = await usage(db, scope)
    funnel = {step["key"]: step for step in conv["funnel"]}
    return {
        "kpis": {
            "calls": funnel["calls"]["count"],
            "answered": funnel["answered"]["count"],
            "engaged_rate": funnel["engaged"]["rate_of_previous"],
            "leads": funnel["lead"]["count"],
            "escalated": conv["outcomes"]["escalated"],
            "response_p50_s": perf["latency"]["response_s"]["p50"],
            "phantom_rate": aud["interruptions"]["phantom_rate"],
            "clarifications_per_call": conv["understanding"]["clarifications_per_call"],
            "cost_inr": use["totals"]["cost_inr"],
            "cost_per_answered_call_inr": use["totals"]["cost_per_answered_call_inr"],
        },
        "funnel": conv["funnel"],
        "daily_cost": use["daily"],
        "decisions": aud["decisions"],
        "coverage": {**conv["coverage"], "calls_with_audio_telemetry": aud["coverage"]["calls_with_audio_telemetry"]},
    }
