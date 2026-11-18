"""Regression tests for runtime lifecycle and background-task supervision."""
import asyncio

import pytest

from katherine_runtime import (
    RuntimeLifecycle,
    RuntimePhase,
    RuntimeTransitionError,
    TaskLimitReached,
    TaskSupervisor,
)


def test_runtime_lifecycle_records_ready_degraded_and_stopped_states():
    now = [100.0]
    runtime = RuntimeLifecycle(clock=lambda: now[0])

    started = runtime.begin_startup()
    assert started.phase is RuntimePhase.STARTING
    assert started.cycle == 1
    assert started.is_ready is False

    now[0] = 102.5
    ready = runtime.mark_ready()
    assert ready.phase is RuntimePhase.READY
    assert ready.is_ready is True
    assert ready.startup_seconds == 2.5

    now[0] = 108.0
    degraded = runtime.mark_degraded()
    assert degraded.phase is RuntimePhase.DEGRADED
    assert degraded.is_ready is False

    runtime.mark_ready()
    now[0] = 110.0
    runtime.begin_shutdown()
    stopped = runtime.mark_stopped()
    assert stopped.phase is RuntimePhase.STOPPED
    assert stopped.uptime_seconds == 10.0
    assert stopped.transition_count == 6


def test_runtime_lifecycle_rejects_invalid_transitions():
    runtime = RuntimeLifecycle()
    with pytest.raises(RuntimeTransitionError):
        runtime.mark_ready()

    runtime.begin_startup()
    with pytest.raises(RuntimeTransitionError):
        runtime.mark_stopped()


def test_runtime_failure_records_only_exception_type_not_message():
    runtime = RuntimeLifecycle()
    runtime.begin_startup()
    failed = runtime.mark_failed(RuntimeError("sensitive provider response"))

    assert failed.phase is RuntimePhase.FAILED
    assert failed.failure_count == 1
    assert failed.last_failure_type == "RuntimeError"
    assert not hasattr(failed, "last_error")
    runtime.begin_shutdown()
    assert runtime.mark_stopped().phase is RuntimePhase.STOPPED


def test_runtime_can_start_a_new_cycle_after_clean_shutdown():
    runtime = RuntimeLifecycle()
    runtime.begin_startup()
    runtime.mark_ready()
    runtime.begin_shutdown()
    runtime.mark_stopped()

    snapshot = runtime.begin_startup()
    assert snapshot.phase is RuntimePhase.STARTING
    assert snapshot.cycle == 2
    assert snapshot.uptime_seconds == 0.0
    assert snapshot.startup_seconds is None


@pytest.mark.asyncio
async def test_task_supervisor_tracks_successful_completion():
    supervisor = TaskSupervisor(max_tasks=2)

    async def work():
        await asyncio.sleep(0)
        return "done"

    task = supervisor.create_task(work(), name="unit-work")
    assert await task == "done"
    await asyncio.sleep(0)

    assert supervisor.active_count == 0
    assert supervisor.completed_count == 1
    assert supervisor.failure_count == 0


@pytest.mark.asyncio
async def test_task_supervisor_cancels_tasks_and_waits_for_cleanup():
    supervisor = TaskSupervisor(max_tasks=2)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = supervisor.create_task(work(), name="long-running")
    await started.wait()
    report = await supervisor.cancel_all(timeout=1.0)

    assert task.cancelled()
    assert cleaned.is_set()
    assert report.cancelled == 1
    assert report.timed_out == 0
    assert report.remaining == 0
    assert supervisor.cancelled_count == 1


@pytest.mark.asyncio
async def test_task_supervisor_enforces_capacity_and_safe_names():
    supervisor = TaskSupervisor(max_tasks=1)
    gate = asyncio.Event()

    async def waiting():
        await gate.wait()

    first = supervisor.create_task(waiting(), name="first")
    with pytest.raises(TaskLimitReached):
        supervisor.create_task(waiting(), name="second")
    with pytest.raises(ValueError):
        supervisor.create_task(waiting(), name="user-id/should-not-be-a-label")

    await supervisor.cancel_all(timeout=1.0)
    assert first.cancelled()


@pytest.mark.asyncio
async def test_task_supervisor_records_failure_class_without_exception_text():
    supervisor = TaskSupervisor()

    async def broken():
        raise RuntimeError("private payload")

    task = supervisor.create_task(broken(), name="test-failure")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert supervisor.failure_count == 1
    assert supervisor.last_failure_type == "RuntimeError"


@pytest.mark.asyncio
async def test_task_supervisor_rejects_invalid_shutdown_timeout():
    supervisor = TaskSupervisor()
    with pytest.raises(ValueError):
        await supervisor.cancel_all(timeout=float("nan"))
    with pytest.raises(ValueError):
        await supervisor.cancel_all(timeout=-1)
