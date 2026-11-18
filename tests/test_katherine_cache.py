"""Tests for the bounded TTL cache and async single-flight coordinator."""
import asyncio

import pytest

from katherine_cache import AsyncSingleFlight, TTLCache


def test_ttl_cache_expires_entries_using_monotonic_clock():
    now = [10.0]
    cache = TTLCache[str, str](
        max_entries=4,
        ttl_seconds=5,
        clock=lambda: now[0],
    )
    cache.set("character", "Cyno")
    assert cache.get("character") == "Cyno"

    now[0] = 15.0
    assert cache.get("character") is None
    snapshot = cache.snapshot()
    assert snapshot.entries == 0
    assert snapshot.expirations == 1
    assert snapshot.hits == 1
    assert snapshot.misses == 1


def test_ttl_cache_evicts_least_recently_used_entry_at_capacity():
    cache = TTLCache[str, int](max_entries=2, ttl_seconds=60)
    cache.set("first", 1)
    cache.set("second", 2)
    assert cache.get("first") == 1
    cache.set("third", 3)

    assert "first" in cache
    assert "second" not in cache
    assert cache.get("third") == 3
    assert cache.snapshot().evictions == 1


def test_ttl_cache_rejects_invalid_limits_and_ttl():
    with pytest.raises(ValueError):
        TTLCache(max_entries=0)
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=-1)
    cache = TTLCache[str, str]()
    with pytest.raises(ValueError):
        cache.set("x", "y", ttl_seconds=float("nan"))


def test_ttl_cache_pop_and_clear_preserve_clear_semantics():
    cache = TTLCache[str, int](max_entries=2)
    cache.set("one", 1)
    assert cache.pop("one") == 1
    assert cache.pop("missing", None) is None
    cache.set("two", 2)
    cache.clear()
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_singleflight_shares_one_result_for_concurrent_callers():
    coordinator = AsyncSingleFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"name": "Cyno"}

    first = asyncio.create_task(coordinator.run("characters:cyno", factory))
    await started.wait()
    second = asyncio.create_task(coordinator.run("characters:cyno", factory))
    await asyncio.sleep(0)
    release.set()
    result_a, result_b = await asyncio.gather(first, second)

    assert calls == 1
    assert result_a == result_b == {"name": "Cyno"}
    assert coordinator.snapshot()["created"] == 1
    assert coordinator.snapshot()["joined"] == 1
    assert coordinator.snapshot()["active"] == 0


@pytest.mark.asyncio
async def test_singleflight_waiter_cancellation_does_not_cancel_shared_request():
    coordinator = AsyncSingleFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "complete"

    cancelled_waiter = asyncio.create_task(coordinator.run("same", factory))
    await started.wait()
    remaining_waiter = asyncio.create_task(coordinator.run("same", factory))
    await asyncio.sleep(0)
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    release.set()
    assert await remaining_waiter == "complete"
    assert calls == 1
    assert coordinator.snapshot()["cancellations"] == 0


@pytest.mark.asyncio
async def test_singleflight_bounds_key_registry_and_bypasses_without_growth():
    coordinator = AsyncSingleFlight(max_inflight_keys=1)
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def slow():
        calls.append("slow")
        started.set()
        await release.wait()
        return "slow"

    async def direct():
        calls.append("direct")
        return "direct"

    first = asyncio.create_task(coordinator.run("first", slow))
    await started.wait()
    second = await coordinator.run("second", direct)
    assert second == "direct"
    assert coordinator.snapshot()["active"] == 1
    assert coordinator.snapshot()["capacity_bypasses"] == 1
    release.set()
    assert await first == "slow"
    assert calls == ["slow", "direct"]
