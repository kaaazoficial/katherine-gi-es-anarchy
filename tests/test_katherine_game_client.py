"""Tests for the dedicated Genshin data API client."""
import asyncio

import pytest

from katherine_game_client import GameAPIClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def make_client(requester):
    return GameAPIClient(
        requester,
        base_url="https://genshin-db-api.example/api/v5",
        metrics={},
        cache_ttl_seconds=30,
        cache_max_entries=4,
    )


@pytest.mark.asyncio
async def test_game_data_caches_successful_entity_and_normalizes_query():
    calls = []

    async def requester(method, url, *, params, timeout):
        calls.append((method, url, params, timeout))
        return FakeResponse({
            "folder": "characters",
            "matchtype": "matchName",
            "result": {"name": "Cyno"},
        })

    client = make_client(requester)
    first = await client.data("characters", "Cyno")
    second = await client.data("characters", "CYNO")

    assert first == second == {"name": "Cyno"}
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/characters")
    assert calls[0][2]["query"] == "cyno"
    assert client.metrics["game_api_cache_misses"] == 1
    assert client.metrics["game_api_cache_hits"] == 1
    assert len(client.cache) == 1


@pytest.mark.asyncio
async def test_game_data_does_not_cache_explicit_no_match_envelopes():
    calls = 0

    async def requester(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse({
            "folder": "characters",
            "matchtype": "none",
            "result": None,
        })

    client = make_client(requester)
    assert await client.data("characters", "NotACharacter") is None
    assert await client.data("characters", "NotACharacter") is None
    assert calls == 2
    assert len(client.cache) == 0


@pytest.mark.asyncio
async def test_game_data_coalesces_identical_concurrent_misses():
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def requester(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return FakeResponse({"name": "Nahida"})

    client = make_client(requester)
    first = asyncio.create_task(client.data("characters", "Nahida"))
    await started.wait()
    second = asyncio.create_task(client.data("characters", "NAHIDA"))
    await asyncio.sleep(0)
    release.set()
    result_a, result_b = await asyncio.gather(first, second)

    assert calls == 1
    assert result_a == result_b == {"name": "Nahida"}
    assert client.metrics["game_api_unique_requests"] == 1
    assert client.metrics["game_api_singleflight_waits"] == 1


@pytest.mark.asyncio
async def test_game_stats_uses_stats_endpoint_and_unwraps_result():
    calls = []

    async def requester(method, url, *, params, timeout):
        calls.append((url, params))
        return FakeResponse({"result": {"name": "Cyno", "hp": 1000}})

    client = make_client(requester)
    result = await client.stats("characters", "Cyno")

    assert result == {"name": "Cyno", "hp": 1000}
    assert calls[0][0].endswith("/stats")
    assert calls[0][1]["folder"] == "characters"


@pytest.mark.asyncio
async def test_game_client_rejects_invalid_folder_and_empty_queries():
    async def unexpected_request(*args, **kwargs):
        raise AssertionError("Invalid input must not reach the network.")

    client = make_client(unexpected_request)
    with pytest.raises(ValueError):
        await client.data("../characters", "Cyno")
    assert await client.data("characters", " ") is None
    assert await client.stats("characters", "") is None


@pytest.mark.asyncio
async def test_game_data_request_failure_is_not_cached():
    calls = 0

    async def requester(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider timeout")

    client = make_client(requester)
    assert await client.data("characters", "Furina") is None
    assert await client.data("characters", "Furina") is None
    assert calls == 2
