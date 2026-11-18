"""Bounded in-process caching and async request coalescing primitives."""
from __future__ import annotations

import asyncio
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_K = TypeVar("_K")
_V = TypeVar("_V")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class CacheSnapshot:
    """Aggregate cache counters; keys and values are never exposed."""

    entries: int
    hits: int
    misses: int
    expirations: int
    evictions: int


class TTLCache(Generic[_K, _V]):
    """An LRU cache with per-entry monotonic expiry and a strict size ceiling.

    Expiration uses a monotonic clock so wall-clock corrections cannot extend or
    shorten an entry unexpectedly. Values are process-local and are never
    persisted to disk.
    """

    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(max_entries, bool) or int(max_entries) < 1:
            raise ValueError("max_entries must be a positive integer")
        ttl = float(ttl_seconds)
        if ttl < 0 or ttl != ttl or ttl == float("inf"):
            raise ValueError("ttl_seconds must be a finite non-negative number")
        self.max_entries = int(max_entries)
        self.ttl_seconds = ttl
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[_K, tuple[float, _V]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._expirations = 0
        self._evictions = 0

    def _purge_expired(self, now: float) -> int:
        expired = [
            key for key, (expires_at, _) in self._entries.items()
            if expires_at <= now
        ]
        for key in expired:
            self._entries.pop(key, None)
        self._expirations += len(expired)
        return len(expired)

    def get(self, key: _K, default: Any = None) -> _V | Any:
        """Return a fresh value and refresh its LRU position."""
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return default
        expires_at, value = entry
        if expires_at <= now:
            self._entries.pop(key, None)
            self._expirations += 1
            self._misses += 1
            return default
        self._entries.move_to_end(key)
        self._hits += 1
        return value

    def set(
        self,
        key: _K,
        value: _V,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Insert or replace a value, evicting the least recently used entry."""
        ttl = self.ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        if ttl < 0 or ttl != ttl or ttl == float("inf"):
            raise ValueError("ttl_seconds must be a finite non-negative number")
        if ttl == 0:
            self._entries.pop(key, None)
            return
        expires_at = self._clock() + ttl
        self._entries[key] = (expires_at, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self._evictions += 1

    def pop(self, key: _K, default: Any = _MISSING) -> _V | Any:
        """Remove a value without returning its expiry metadata."""
        self._purge_expired(self._clock())
        entry = self._entries.pop(key, _MISSING)
        if entry is _MISSING:
            if default is _MISSING:
                raise KeyError(key)
            return default
        return entry[1]

    def prune(self) -> int:
        """Remove every expired entry and return the number removed."""
        return self._purge_expired(self._clock())

    def clear(self, *, reset_counters: bool = False) -> None:
        self._entries.clear()
        if reset_counters:
            self._hits = 0
            self._misses = 0
            self._expirations = 0
            self._evictions = 0

    def snapshot(self) -> CacheSnapshot:
        self.prune()
        return CacheSnapshot(
            entries=len(self._entries),
            hits=self._hits,
            misses=self._misses,
            expirations=self._expirations,
            evictions=self._evictions,
        )

    def __contains__(self, key: object) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __len__(self) -> int:
        self.prune()
        return len(self._entries)

    def __iter__(self) -> Iterator[_K]:
        self.prune()
        return iter(tuple(self._entries))

    def items(self) -> tuple[tuple[_K, _V], ...]:
        self.prune()
        return tuple((key, value) for key, (_, value) in self._entries.items())


class AsyncSingleFlight:
    """Share an in-flight async operation per key and per event loop.

    Awaiters are shielded from each other's cancellation. Completed tasks are
    removed immediately and only aggregate lifecycle counters are retained.
    When the distinct-key ceiling is reached, a request bypasses coalescing
    rather than allowing an attacker to grow an unbounded task registry.
    """

    def __init__(self, *, max_inflight_keys: int = 256) -> None:
        if isinstance(max_inflight_keys, bool) or int(max_inflight_keys) < 1:
            raise ValueError("max_inflight_keys must be a positive integer")
        self.max_inflight_keys = int(max_inflight_keys)
        self._by_loop: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[Any, asyncio.Task[Any]]
        ] = weakref.WeakKeyDictionary()
        self._created = 0
        self._joined = 0
        self._capacity_bypasses = 0
        self._failures = 0
        self._cancellations = 0

    async def run(
        self,
        key: Any,
        factory: Callable[[], Awaitable[_V]],
        *,
        on_create: Callable[[], None] | None = None,
        on_join: Callable[[], None] | None = None,
    ) -> _V:
        """Run a factory once for concurrent callers with the same key."""
        loop = asyncio.get_running_loop()
        tasks = self._by_loop.get(loop)
        if tasks is None:
            tasks = {}
            self._by_loop[loop] = tasks

        task = tasks.get(key)
        if task is not None and not task.done():
            self._joined += 1
            if on_join is not None:
                on_join()
            return await asyncio.shield(task)

        if len(tasks) >= self.max_inflight_keys:
            self._capacity_bypasses += 1
            self._created += 1
            if on_create is not None:
                on_create()
            return await factory()

        task = loop.create_task(factory(), name="katheryne-singleflight")
        tasks[key] = task
        self._created += 1
        if on_create is not None:
            on_create()

        def discard(done: asyncio.Task[Any]) -> None:
            if tasks.get(key) is done:
                tasks.pop(key, None)
            if not tasks:
                self._by_loop.pop(loop, None)
            if done.cancelled():
                self._cancellations += 1
                return
            try:
                error = done.exception()
            except asyncio.CancelledError:
                self._cancellations += 1
                return
            if error is not None:
                self._failures += 1

        task.add_done_callback(discard)
        return await asyncio.shield(task)

    def snapshot(self) -> dict[str, int]:
        active = sum(
            not task.done()
            for tasks in tuple(self._by_loop.values())
            for task in tuple(tasks.values())
        )
        return {
            "active": active,
            "created": self._created,
            "joined": self._joined,
            "capacity_bypasses": self._capacity_bypasses,
            "failures": self._failures,
            "cancellations": self._cancellations,
        }

    def clear_completed(self) -> int:
        """Prune completed task references from all still-live event loops."""
        removed = 0
        for loop, tasks in tuple(self._by_loop.items()):
            for key, task in tuple(tasks.items()):
                if task.done():
                    tasks.pop(key, None)
                    removed += 1
            if not tasks:
                self._by_loop.pop(loop, None)
        return removed
