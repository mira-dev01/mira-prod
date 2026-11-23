"""Where, in a call's transcript, Mira escalated to the host.

The saved transcript is only the spoken turns (tool-call turns are dropped, see voice/pipeline.py), so
afterwards there is no telling which lines were the escalation. The pipeline therefore reads the live LLM context
once, as the call ends, and keeps a small marker for every tool that hands something to the host: the guest's
line that led to it and what Mira said right after. The dashboard (Tickets -> Transcript) colours those
lines. Text, not positions, because the dashboard merges multi-line turns and would otherwise drift off by one.

Nothing here raises into the end-of-call work: a context it cannot read gives no markers, and the transcript is
saved exactly as before.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The tools whose result is "the host will deal with this" (each opens a ticket; see tool_handlers.py).
ESCALATION_TOOLS = frozenset({"escalate_to_host", "request_host_transfer", "dispatch_technician", "request_early_late_checkout"})
MAX_MARKERS = 20


def _text(message: dict) -> str | None:
    content = message.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def _tool_names(message: dict) -> list[str]:
    names = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


def build_markers(messages: list[dict]) -> list[dict]:
    """[{"tool": ..., "guest": <the guest's line before, or None>, "mira": <Mira's line, or None>}, ...] in call
    order. Mira's line is what she said alongside the tool call, else her next spoken turn."""
    markers: list[dict] = []
    try:
        for i, message in enumerate(messages):
            if message.get("role") != "assistant":
                continue
            tools = [name for name in _tool_names(message) if name in ESCALATION_TOOLS]
            if not tools:
                continue
            guest = next((_text(m) for m in reversed(messages[:i]) if m.get("role") == "user" and _text(m)), None)
            mira = _text(message) or next(
                (_text(m) for m in messages[i + 1 :] if m.get("role") == "assistant" and _text(m)), None
            )
            markers.append({"tool": tools[0], "guest": guest, "mira": mira})
            if len(markers) >= MAX_MARKERS:
                break
    except Exception:
        logger.exception("could not read escalation markers from the call context")
        return []
    return markers
