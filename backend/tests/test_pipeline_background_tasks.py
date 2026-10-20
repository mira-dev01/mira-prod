import asyncio
import gc

from app.voice import pipeline


async def test_spawn_background_task_survives_gc_with_no_external_reference():
    # Regression for the normal-call-termination fix's on_pipeline_finished
    # hangup task: asyncio.create_task()'s own docs warn that a Task with no
    # surviving reference "can be garbage collected at any time, even before
    # it's done." _spawn_background_task exists specifically to hold that
    # reference (in pipeline._background_tasks) so a bare, unassigned
    # asyncio.create_task(...) call site can't silently lose its task to GC
    # mid-flight. Forcing a real gc.collect() while the task is still
    # in-flight and confirming it still completes is the actual property
    # that matters here, not just "the function returns a Task".
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def _slow_coro():
        started.set()
        await release.wait()
        completed.set()

    pipeline._spawn_background_task(_slow_coro())
    # No local variable holds the Task -- if _spawn_background_task didn't
    # keep its own reference, this would be exactly the unreferenced-task
    # GC hazard being tested for.

    await started.wait()
    gc.collect()  # force collection while the task is still in-flight
    await asyncio.sleep(0.05)
    assert not completed.is_set()  # still running, not silently dropped

    release.set()
    await asyncio.sleep(0.05)
    assert completed.is_set()


async def test_spawn_background_task_removes_itself_from_the_registry_once_done():
    # The registry must not grow unboundedly across a long-lived process --
    # each task removes itself via its own done-callback once finished.
    async def _noop():
        pass

    task = pipeline._spawn_background_task(_noop())
    assert task in pipeline._background_tasks

    await task
    await asyncio.sleep(0)  # let the done-callback (scheduled via call_soon) run

    assert task not in pipeline._background_tasks


async def test_spawn_background_task_exception_does_not_propagate_to_caller():
    # Same fire-and-forget contract as the pre-existing _update_guest_memory
    # pattern this mirrors: a failure inside the background task must never
    # raise into whoever called _spawn_background_task.
    async def _boom():
        raise RuntimeError("simulated failure inside a detached task")

    task = pipeline._spawn_background_task(_boom())
    await asyncio.sleep(0.05)

    assert task.done()
    assert isinstance(task.exception(), RuntimeError)
    # The exception is retrieved above (task.exception()), so asyncio won't
    # log an "exception was never retrieved" warning for it either.
