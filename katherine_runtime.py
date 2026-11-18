"""Process lifecycle and bounded background-task supervision.

The runtime layer stores operational state only. It never accepts user IDs,
chat content, prompts, or provider payloads.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, TypeVar

_T = TypeVar("_T")
_TASK_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}\\Z")


class RuntimePhase(str, Enum):
    """Observable states of one application process."""

    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimeTransitionError(RuntimeError):
    """Raised when an invalid lifecycle transition is requested."""


class TaskLimitReached(RuntimeError):
    """Raised when the bounded background-task registry is full."""


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Privacy-safe operational snapshot; timestamps are represented as durations."""

    phase: RuntimePhase
    cycle: int
    uptime_seconds: float
    startup_seconds: float | None
    transition_count: int
    failure_count: int
    last_failure_type: str | None

    @property
    def is_ready(self) -> bool:
        return self.phase is RuntimePhase.READY


class RuntimeLifecycle:
    """Thread-safe state machine for the process startup and shutdown lifecycle."""

    _ALLOWED: ClassVar[dict[RuntimePhase, frozenset[RuntimePhase]]] = {
        RuntimePhase.NEW: frozenset({RuntimePhase.STARTING}),
        RuntimePhase.STARTING: frozenset({
            RuntimePhase.READY,
            RuntimePhase.DEGRADED,
            RuntimePhase.STOPPING,
            RuntimePhase.FAILED,
        }),
        RuntimePhase.READY: frozenset({
            RuntimePhase.DEGRADED,
            RuntimePhase.STOPPING,
            RuntimePhase.FAILED,
        }),
        RuntimePhase.DEGRADED: frozenset({
            RuntimePhase.READY,
            RuntimePhase.STOPPING,
            RuntimePhase.FAILED,
        }),
        RuntimePhase.STOPPING: frozenset({
            RuntimePhase.STOPPED,
            RuntimePhase.FAILED,
        }),
        RuntimePhase.FAILED: frozenset({
            RuntimePhase.STOPPING,
            RuntimePhase.STOPPED,
        }),
        RuntimePhase.STOPPED: frozenset({RuntimePhase.STARTING}),
    }

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._phase = RuntimePhase.NEW
        self._started_at: float | None = None
        self._ready_at: float | None = None
        self._stopped_at: float | None = None
        self._last_transition_at = self._clock()
        self._transition_count = 0
        self._cycle = 0
        self._failure_count = 0
        self._last_failure_type: str | None = None
        # Startup and shutdown are initiated by the event loop, while health
        # requests may be served from a different execution context.
        import threading

        self._lock = threading.RLock()

    def _transition(self, target: RuntimePhase) -> None:
        allowed = self._ALLOWED[self._phase]
        if target not in allowed:
            raise RuntimeTransitionError(
                f"Invalid runtime transition: {self._phase.value} -> {target.value}"
            )
        self._phase = target
        self._last_transition_at = self._clock()
        self._transition_count += 1

    def begin_startup(self) -> RuntimeSnapshot:
        with self._lock:
            self._transition(RuntimePhase.STARTING)
            now = self._last_transition_at
            self._started_at = now
            self._ready_at = None
            self._stopped_at = None
            self._last_failure_type = None
            self._cycle += 1
            return self._snapshot_locked(now)

    def mark_ready(self) -> RuntimeSnapshot:
        with self._lock:
            self._transition(RuntimePhase.READY)
            self._ready_at = self._last_transition_at
            return self._snapshot_locked(self._last_transition_at)

    def mark_degraded(self) -> RuntimeSnapshot:
        with self._lock:
            self._transition(RuntimePhase.DEGRADED)
            return self._snapshot_locked(self._last_transition_at)

    def mark_failed(self, error: BaseException) -> RuntimeSnapshot:
        with self._lock:
            self._transition(RuntimePhase.FAILED)
            # Store only the exception class. Exception messages can contain
            # request data, provider responses, credentials, or user content.
            self._last_failure_type = type(error).__name__[:80]
            self._failure_count += 1
            return self._snapshot_locked(self._last_transition_at)

    def begin_shutdown(self) -> RuntimeSnapshot:
        with self._lock:
            if self._phase in {RuntimePhase.STOPPING, RuntimePhase.STOPPED}:
                return self._snapshot_locked(self._clock())
            self._transition(RuntimePhase.STOPPING)
            return self._snapshot_locked(self._last_transition_at)

    def mark_stopped(self) -> RuntimeSnapshot:
        with self._lock:
            self._transition(RuntimePhase.STOPPED)
            self._stopped_at = self._last_transition_at
            return self._snapshot_locked(self._last_transition_at)

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot_locked(self._clock())

    def _snapshot_locked(self, now: float) -> RuntimeSnapshot:
        start = self._started_at
        end = self._stopped_at if self._stopped_at is not None else now
        uptime = max(0.0, end - start) if start is not None else 0.0
        startup = (
            max(0.0, self._ready_at - start)
            if self._ready_at is not None and start is not None
            else None
        )
        return RuntimeSnapshot(
            phase=self._phase,
            cycle=self._cycle,
            uptime_seconds=round(uptime, 6),
            startup_seconds=round(startup, 6) if startup is not None else None,
            transition_count=self._transition_count,
            failure_count=self._failure_count,
            last_failure_type=self._last_failure_type,
        )


@dataclass(frozen=True, slots=True)
class TaskShutdownReport:
    """Summary of a bounded cancellation pass without task payloads."""

    cancelled: int
    timed_out: int
    remaining: int


class TaskSupervisor:
    """Own a bounded set of process-local tasks and collect safe failure metadata."""

    def __init__(self, *, max_tasks: int = 64) -> None:
        if isinstance(max_tasks, bool) or int(max_tasks) < 1:
            raise ValueError("max_tasks must be a positive integer")
        self.max_tasks = int(max_tasks)
        self._tasks: dict[asyncio.Task[Any], str] = {}
        self._completed_count = 0
        self._cancelled_count = 0
        self._failure_count = 0
        self._last_failure_type: str | None = None

    @property
    def active_count(self) -> int:
        return sum(not task.done() for task in self._tasks)

    @property
    def completed_count(self) -> int:
        return self._completed_count

    @property
    def cancelled_count(self) -> int:
        return self._cancelled_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def last_failure_type(self) -> str | None:
        return self._last_failure_type

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, _T],
        *,
        name: str,
    ) -> asyncio.Task[_T]:
        """Create and supervise a coroutine using a bounded, non-PII task label."""
        label = str(name or "")
        if not _TASK_NAME.fullmatch(label):
            coroutine.close()
            raise ValueError("Task names must be 1-64 safe ASCII characters.")
        if any(existing == label for existing in self._tasks.values()):
            coroutine.close()
            raise ValueError(f"An active task already uses the name {label!r}.")
        if self.active_count >= self.max_tasks:
            coroutine.close()
            raise TaskLimitReached(
                f"Background task limit reached ({self.max_tasks})."
            )
        task = asyncio.create_task(coroutine, name=f"katheryne:{label}")
        self._tasks[task] = label
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.pop(task, None)
        if task.cancelled():
            self._cancelled_count += 1
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            self._cancelled_count += 1
            return
        if error is not None:
            self._failure_count += 1
            self._last_failure_type = type(error).__name__[:80]
        else:
            self._completed_count += 1

    async def cancel_all(self, *, timeout: float = 5.0) -> TaskShutdownReport:
        """Cancel owned tasks and wait for cooperative cleanup for a bounded time."""
        try:
            wait_seconds = float(timeout)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("timeout must be a finite non-negative number") from exc
        if not math.isfinite(wait_seconds) or wait_seconds < 0:
            raise ValueError("timeout must be a finite non-negative number")

        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()

        pending: set[asyncio.Task[Any]] = set()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=wait_seconds)
            for task in pending:
                task.cancel()
            if pending and wait_seconds > 0:
                _, pending = await asyncio.wait(
                    pending, timeout=min(0.25, wait_seconds)
                )

        # Let done callbacks consume exceptions and remove completed tasks.
        await asyncio.sleep(0)
        return TaskShutdownReport(
            cancelled=sum(task.cancelled() for task in tasks),
            timed_out=sum(not task.done() for task in pending),
            remaining=self.active_count,
        )
