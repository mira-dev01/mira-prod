"""The one contract every voice-agent tool call goes through:

    LLM args -> validation (against the function's own signature) -> tool
    body -> exactly one structured result envelope -> LLM

Why this exists (all confirmed against pipecat 1.6's own code):

- Pipecat invokes a direct function as `function(params=..., **llm_args)`
  with NO validation. An argument the LLM invents, or a required one it
  omits, raises TypeError before any of our code runs. Confirmed live
  2026-09-25: recommend_properties(..., nights=1) died exactly this way.
- When a tool raises, pipecat logs it and pushes a non-fatal ErrorFrame but
  never calls result_callback -- the tool message stays "IN_PROGRESS" in the
  LLM context forever and the model improvises a result.
- Any falsy result is written to context as "COMPLETED" -- indistinguishable
  from success.
- `Literal` type hints render as an untyped `{}` schema, so the LLM never
  sees the allowed values (e.g. escalate_to_host's urgency) -- an off-list
  value used to fail Pydantic deep inside the wrapper and come back as a
  guest-facing "could you repeat the dates?", silently dropping escalations.

The validation model is generated from the SAME inspect.signature() pipecat
turns into the LLM schema, so schema and validation can never drift apart.
Reuses Pydantic (already the project's tool-arg layer); no new infrastructure.

Envelope statuses (one of each is decided in exactly one place):
- ok / no_results / needs_clarification / business_error: the tool body
  decides, by passing a ToolOutcome to result_callback (a plain str is "ok").
- invalid_tool_arguments / internal_error: decided here, never by a tool.
"""

import functools
import inspect
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, get_type_hints

from loguru import logger
from pydantic import ConfigDict, ValidationError, create_model

from app.schemas.tool import ToolBusinessError

ToolStatus = Literal[
    "ok",
    "no_results",
    "needs_clarification",
    "business_error",
    "invalid_tool_arguments",
    "internal_error",
]
_SUCCESS_STATUSES = frozenset({"ok", "no_results", "needs_clarification"})

# After this many invalid-argument results for the same tool in a row, the
# model is told to stop retrying it -- a model stuck re-sending the same bad
# call would otherwise loop with dead air on a live call.
MAX_CONSECUTIVE_INVALID_CALLS = 2

_DEFAULT_INTERNAL_ERROR_TEXT = (
    "This tool failed because of a system error -- NOT an empty or negative result. You have no "
    "information from it: do not invent or guess what it would have returned. Tell the guest you "
    "couldn't do that just now and offer to have the host follow up."
)


@dataclass(frozen=True)
class ToolOutcome:
    status: ToolStatus
    text: str

    @classmethod
    def ok(cls, text: str) -> "ToolOutcome":
        return cls("ok", text)

    @classmethod
    def no_results(cls, text: str) -> "ToolOutcome":
        return cls("no_results", text)

    @classmethod
    def needs_clarification(cls, text: str) -> "ToolOutcome":
        return cls("needs_clarification", text)

    @classmethod
    def business_error(cls, text: str) -> "ToolOutcome":
        return cls("business_error", text)

    @classmethod
    def invalid_arguments(cls, error: ValidationError | str) -> "ToolOutcome":
        detail = describe_validation_error(error) if isinstance(error, ValidationError) else error
        return cls("invalid_tool_arguments", detail)


def describe_validation_error(exc: ValidationError) -> str:
    """Short, model-actionable summary -- names the bad field and, for a
    Literal, Pydantic's own message already lists the allowed values."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "arguments"
        if err["type"] == "extra_forbidden":
            parts.append(f"'{loc}' is not a parameter of this tool")
        elif err["type"] == "missing":
            parts.append(f"'{loc}' is required")
        else:
            parts.append(f"'{loc}': {err['msg']}")
    return "; ".join(parts)


def build_envelope(tool: str, outcome: ToolOutcome) -> dict[str, Any]:
    success = outcome.status in _SUCCESS_STATUSES
    envelope: dict[str, Any] = {"success": success, "status": outcome.status, "tool": tool}
    if success:
        envelope["result"] = outcome.text
    else:
        envelope["error_type"] = outcome.status
        envelope["message"] = outcome.text
    return envelope


def _llm_args_model(func: Callable) -> type:
    """A Pydantic model of the LLM-facing parameters: everything after the
    special first `params` argument, exactly as pipecat's
    DirectFunctionWrapper reads them. extra="forbid" is what rejects an
    invented argument (user decision D1: reject, don't silently drop)."""
    hints = get_type_hints(func)
    fields: dict[str, Any] = {}
    for param in list(inspect.signature(func).parameters.values())[1:]:
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (hints.get(param.name, Any), default)
    return create_model(f"{func.__name__}_llm_args", __config__=ConfigDict(extra="forbid"), **fields)


def voice_tool(
    *,
    call_session_id: uuid.UUID | None = None,
    aliases: dict[str, str] | None = None,
    internal_error_text: str | None = None,
    on_internal_error: Callable[[], None] | None = None,
):
    """aliases: legacy/misremembered argument names mapped onto the real one
    before validation (e.g. {"budget": "budget_amount"}).
    internal_error_text/on_internal_error: a tool-specific failure message,
    and a hook for tool-specific cleanup (recommend_properties arms the
    recommendation guard's failure override with it)."""
    aliases = aliases or {}

    def decorator(func):
        name = func.__name__
        args_model = _llm_args_model(func)
        valid_names = ", ".join(args_model.model_fields) or "none"
        invalid_streak = 0

        @functools.wraps(func)
        async def wrapper(params, **llm_args):
            nonlocal invalid_streak
            started = time.monotonic()
            delivered: list[ToolOutcome] = []
            original_callback = params.result_callback

            async def deliver(outcome: ToolOutcome, properties=None) -> None:
                nonlocal invalid_streak
                if delivered:
                    logger.warning("voice_tool {}: ignoring a second result for the same call", name)
                    return
                if outcome.status == "invalid_tool_arguments":
                    invalid_streak += 1
                    guidance = f"Valid parameters: {valid_names}."
                    if invalid_streak >= MAX_CONSECUTIVE_INVALID_CALLS:
                        guidance += f" Do not call {name} again right now -- carry on the conversation without it."
                    else:
                        guidance += " Fix the arguments and call it again."
                    outcome = ToolOutcome(outcome.status, f"Invalid arguments for {name}: {outcome.text}. {guidance}")
                elif outcome.status in _SUCCESS_STATUSES:
                    invalid_streak = 0
                delivered.append(outcome)
                logger.info(
                    "voice_tool_call tool={} call_session_id={} tool_call_id={} success={} status={} "
                    "duration_ms={} arg_keys={}",
                    name,
                    call_session_id,
                    getattr(params, "tool_call_id", None),
                    outcome.status in _SUCCESS_STATUSES,
                    outcome.status,
                    int((time.monotonic() - started) * 1000),
                    sorted(llm_args),
                )
                envelope = build_envelope(name, outcome)
                if properties is not None:
                    await original_callback(envelope, properties=properties)
                else:
                    await original_callback(envelope)

            async def result_callback(result, *, properties=None) -> None:
                if isinstance(result, ToolOutcome):
                    outcome = result
                elif isinstance(result, ToolBusinessError):
                    outcome = ToolOutcome.business_error(str(result))
                else:
                    outcome = ToolOutcome.ok(str(result))
                await deliver(outcome, properties)

            for old, new in aliases.items():
                if old in llm_args and new not in llm_args:
                    llm_args[new] = llm_args.pop(old)
            try:
                validated = args_model.model_validate(llm_args)
            except ValidationError as exc:
                await deliver(ToolOutcome.invalid_arguments(exc))
                return

            params.result_callback = result_callback
            try:
                # Only the fields the model actually supplied -- a wrapper's
                # own defaults (and its None-means-"not said" backfill
                # semantics) stay exactly as before.
                await func(params, **{k: getattr(validated, k) for k in validated.model_fields_set})
            except ValidationError as exc:
                # A wrapper's own *Args model rejected a value the signature
                # allowed (a field validator, e.g. an unparseable date).
                await deliver(ToolOutcome.invalid_arguments(exc))
            except Exception:
                logger.exception(
                    "voice_tool {} raised (call_session_id={}, tool_call_id={})",
                    name,
                    call_session_id,
                    getattr(params, "tool_call_id", None),
                )
                if on_internal_error is not None and not delivered:
                    on_internal_error()
                await deliver(ToolOutcome("internal_error", internal_error_text or _DEFAULT_INTERNAL_ERROR_TEXT))
            finally:
                params.result_callback = original_callback
            if not delivered:
                logger.error("voice_tool {} returned without a result", name)
                await deliver(ToolOutcome("internal_error", internal_error_text or _DEFAULT_INTERNAL_ERROR_TEXT))

        return wrapper

    return decorator
