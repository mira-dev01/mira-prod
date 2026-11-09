"""on_pipeline_finished's post-call work runs as isolated steps: one failure
must not skip the rest, must leave a traceback in the logs, and a step that
depends on an earlier result is skipped -- never run on a guess."""

import logging
import uuid

import pytest

from app.voice.pipeline import _PostCallSteps


class _FakeDb:
    def __init__(self, rollback_error: Exception | None = None):
        self.rollbacks = 0
        self._rollback_error = rollback_error

    async def rollback(self):
        self.rollbacks += 1
        if self._rollback_error:
            raise self._rollback_error


async def test_a_failing_step_does_not_stop_later_steps():
    steps = _PostCallSteps(uuid.uuid4())
    ran = []

    async with steps.step("classify"):
        raise RuntimeError("db write failed")
    async with steps.step("summarize"):
        ran.append("summarize")
    async with steps.step("telemetry"):
        ran.append("telemetry")

    assert ran == ["summarize", "telemetry"]
    assert steps.failed == ["classify"]
    assert steps.completed == ["summarize", "telemetry"]


async def test_failure_is_logged_with_a_traceback(caplog):
    steps = _PostCallSteps(uuid.uuid4())
    with caplog.at_level(logging.ERROR):
        async with steps.step("classify"):
            raise ValueError("boom")
    records = [r for r in caplog.records if "post_call_step_failed" in r.getMessage()]
    assert records
    record = records[0]
    assert "step=classify" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[0] is ValueError


async def test_the_shared_session_is_rolled_back_after_a_failed_step():
    db = _FakeDb()
    steps = _PostCallSteps(uuid.uuid4())
    async with steps.step("classify", db):
        raise RuntimeError("statement failed")
    assert db.rollbacks == 1


async def test_a_failing_rollback_does_not_escape():
    db = _FakeDb(rollback_error=RuntimeError("connection gone"))
    steps = _PostCallSteps(uuid.uuid4())
    async with steps.step("classify", db):
        raise RuntimeError("statement failed")
    async with steps.step("next"):
        pass
    assert steps.completed == ["next"]


async def test_a_dependent_step_is_skipped_when_its_input_is_missing():
    steps = _PostCallSteps(uuid.uuid4())
    ran = []
    summary = None  # the summary step failed earlier

    async with steps.step("reconcile_promises"):
        steps.require(summary, "summary")
        ran.append("reconcile")

    assert ran == []
    assert steps.skipped == ["reconcile_promises"]
    assert steps.failed == []  # a skip is not a failure


async def test_lead_cleanup_never_runs_on_an_unknown_classification():
    """The dangerous case the gating exists for: classification unknown must
    not be treated as 'unqualified' and delete a real lead."""
    steps = _PostCallSteps(uuid.uuid4())
    deleted = []
    classification = None

    async with steps.step("unqualified_lead_cleanup"):
        steps.require(classification, "classification")
        deleted.append("lead")

    assert deleted == []


async def test_cancellation_is_not_swallowed():
    import asyncio

    steps = _PostCallSteps(uuid.uuid4())
    with pytest.raises(asyncio.CancelledError):
        async with steps.step("classify"):
            raise asyncio.CancelledError()


async def test_summary_line_lists_what_finished_and_what_did_not(caplog):
    steps = _PostCallSteps(uuid.uuid4())
    async with steps.step("a"):
        pass
    async with steps.step("b"):
        raise RuntimeError("x")
    with caplog.at_level(logging.INFO):
        steps.log_summary()
    message = caplog.records[-1].getMessage()
    assert "completed=['a']" in message and "failed=['b']" in message
