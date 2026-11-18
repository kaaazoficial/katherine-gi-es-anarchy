"""Genshin data API client with bounded caching and request coalescing."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from katherine_cache import AsyncSingleFlight, TTLCache
from katherine_game_queries import strip_game_scope_qualifier, unwrap_genshin_db_response

RequestCallable = Callable[..., Awaitable[Any]]
_EMPTY = object()
_FOLDER_PATTERN = re.compile(r"[a-z0-9_-]{1,40}\\Z")


class GameAPIClient:
    """Own Genshin API transport policy, cache lifecycle, and duplicate suppression."""

    def __init__(
        self,
        requester: RequestCallable,
        *,
        base_url: str,
        metrics: MutableMapping[str, Any],
        cache_ttl_seconds: float = 21_600.0,
        cache_max_entries: int = 400,
        max_inflight_keys: int = 256,
        logger: logging.Logger | None = None,
    ) -> None:
        self._requester = requester
        self.base_url = str(base_url or "").rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("The game API base URL must use HTTPS.")
        self.metrics = metrics
        self.cache: TTLCache[str, Any] = TTLCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )
        self.singleflight = AsyncSingleFlight(max_inflight_keys=max_inflight_keys)
        self.logger = logger or logging.getLogger(__name__)

    def _increment(self, key: str) -> None:
        self.metrics[key] = int(self.metrics.get(key, 0) or 0) + 1

    def _key(self, namespace: str, folder: str, query: str, params: dict[str, Any]) -> str:
        options = json.dumps(
            params,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return f"{namespace}:{folder}:{query.casefold()}:{options}"

    @staticmethod
    def _validate_folder(folder: str) -> str:
        candidate = str(folder or "").strip().casefold()
        if not _FOLDER_PATTERN.fullmatch(candidate):
            raise ValueError("Invalid Genshin API folder.")
        return candidate

    async def data(
        self,
        folder: str,
        query: str,
        extra_params: dict[str, Any] | None = None,
    ) -> Any:
        """Fetch one entity/list payload and cache only a real matched result."""
        folder_name = self._validate_folder(folder)
        clean_query = strip_game_scope_qualifier(query)
        clean_query = clean_query or str(query or "").strip()
        clean_query = re.sub(r"\\s+", " ", clean_query).strip()
        if not clean_query or len(clean_query) > 200:
            return None

        supplied = dict(extra_params or {})
        key = self._key("data", folder_name, clean_query, supplied)
        cached = self.cache.get(key, _EMPTY)
        if cached is not _EMPTY:
            self._increment("game_api_cache_hits")
            return cached
        self._increment("game_api_cache_misses")

        async def fetch() -> Any:
            params: dict[str, Any] = {
                "query": clean_query,
                "dumpResult": "true",
                "matchNames": "true",
                "matchAltNames": "true",
                "matchAliases": "true",
                "queryLanguages": "english,spanish",
                "resultLanguage": "spanish",
            }
            params.update(supplied)
            try:
                response = await self._requester(
                    "GET",
                    f"{self.base_url}/{folder_name}",
                    params=params,
                    timeout=18,
                )
                response.raise_for_status()
                data = unwrap_genshin_db_response(response.json())
                if data is None or data == {} or data == []:
                    return None
                self.cache.set(key, data)
                return data
            except Exception as exc:
                self.logger.warning(
                    "Genshin API %s request failed: %s",
                    folder_name,
                    type(exc).__name__,
                )
                return None

        return await self.singleflight.run(
            key,
            fetch,
            on_create=lambda: self._increment("game_api_unique_requests"),
            on_join=lambda: self._increment("game_api_singleflight_waits"),
        )

    async def stats(self, folder: str, query: str) -> Any:
        """Fetch normalized game statistics and cache successful dictionaries."""
        folder_name = self._validate_folder(folder)
        clean_query = re.sub(r"\\s+", " ", str(query or "")).strip()
        if not clean_query or len(clean_query) > 200:
            return None

        params = {
            "folder": folder_name,
            "query": clean_query,
            "queryLanguages": "english,spanish",
        }
        key = self._key("stats", folder_name, clean_query, params)
        cached = self.cache.get(key, _EMPTY)
        if cached is not _EMPTY:
            self._increment("game_api_cache_hits")
            return cached
        self._increment("game_api_cache_misses")

        async def fetch() -> Any:
            try:
                response = await self._requester(
                    "GET",
                    f"{self.base_url}/stats",
                    params=params,
                    timeout=18,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and isinstance(data.get("result"), dict):
                    data = data["result"]
                if isinstance(data, dict) and data:
                    self.cache.set(key, data)
                    return data
            except Exception as exc:
                self.logger.warning(
                    "Genshin stats request failed: %s",
                    type(exc).__name__,
                )
            return None

        return await self.singleflight.run(
            key,
            fetch,
            on_create=lambda: self._increment("game_api_unique_requests"),
            on_join=lambda: self._increment("game_api_singleflight_waits"),
        )

    def cleanup(self) -> int:
        """Expire cached data and stale completed single-flight references."""
        expired = self.cache.prune()
        self.singleflight.clear_completed()
        return expired
