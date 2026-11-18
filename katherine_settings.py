"""Room settings repository with a bounded, short-lived process cache."""
from __future__ import annotations
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any


class ChatSettingsStore:
    """Read and write room settings with bounded TTL caching and batched reads."""
    def __init__(self, *, db_session: Callable[[], Any],
                 metrics: MutableMapping[str, int], ttl_seconds: float = 15.0,
                 max_entries: int = 2048,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if ttl_seconds <= 0 or max_entries < 1:
            raise ValueError("Invalid room settings cache limits.")
        self.db_session = db_session
        self.metrics = metrics
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self.clock = clock
        self.cache: dict[tuple[str, str], tuple[float, str | None]] = {}
        self._lock = threading.RLock()

    def _metric(self, name: str) -> None:
        self.metrics[name] = int(self.metrics.get(name, 0)) + 1

    def _put(self, key: tuple[str, str], value: str | None, now: float) -> None:
        with self._lock:
            if key not in self.cache and len(self.cache) >= self.max_entries:
                oldest = min(self.cache, key=lambda item: self.cache[item][0])
                self.cache.pop(oldest, None)
            self.cache[key] = (now + self.ttl_seconds, value)

    def get(self, thread_id: str, key: str, default: str = "") -> str:
        cache_key = (str(thread_id), str(key))
        now = self.clock()
        with self._lock:
            cached = self.cache.get(cache_key)
            if cached is not None and cached[0] > now:
                self._metric("chat_setting_cache_hits")
                return str(cached[1]) if cached[1] is not None else str(default)
            self.cache.pop(cache_key, None)
        self._metric("chat_setting_cache_misses")
        with self.db_session() as db:
            row = db.execute(
                "SELECT setting_value FROM chat_settings WHERE thread_id=? AND setting_key=?",
                cache_key,
            ).fetchone()
        stored = str(row["setting_value"]) if row else None
        self._put(cache_key, stored, now)
        return stored if stored is not None else str(default)

    def get_many(self, thread_id: str, defaults: Mapping[str, str]) -> dict[str, str]:
        room = str(thread_id)
        normalized = {str(key): str(value) for key, value in defaults.items()}
        if not normalized:
            return {}
        now = self.clock()
        values: dict[str, str] = {}
        missing: list[str] = []
        with self._lock:
            for key, default in normalized.items():
                cache_key = (room, key)
                cached = self.cache.get(cache_key)
                if cached is not None and cached[0] > now:
                    self._metric("chat_setting_cache_hits")
                    values[key] = str(cached[1]) if cached[1] is not None else default
                else:
                    self.cache.pop(cache_key, None)
                    self._metric("chat_setting_cache_misses")
                    missing.append(key)
        if not missing:
            return values
        stored: dict[str, str] = {}
        for offset in range(0, len(missing), 500):
            chunk = missing[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            with self.db_session() as db:
                rows = db.execute(
                    "SELECT setting_key,setting_value FROM chat_settings "
                    f"WHERE thread_id=? AND setting_key IN ({placeholders})",
                    (room, *chunk),
                ).fetchall()
            stored.update({str(row["setting_key"]): str(row["setting_value"]) for row in rows})
        for key in missing:
            value = stored.get(key)
            self._put((room, key), value, now)
            values[key] = value if value is not None else normalized[key]
        return values

    def set(self, thread_id: str, key: str, value: str) -> None:
        cache_key = (str(thread_id), str(key))
        stored = str(value)
        with self.db_session() as db:
            db.execute(
                """INSERT INTO chat_settings(thread_id,setting_key,setting_value,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(thread_id,setting_key) DO UPDATE SET
                   setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                (cache_key[0], cache_key[1], stored, time.time()),
            )
        self._put(cache_key, stored, self.clock())

    def prune(self, *, now: float | None = None) -> int:
        current = self.clock() if now is None else float(now)
        with self._lock:
            expired = [key for key, (expiry, _) in self.cache.items() if expiry <= current]
            for key in expired:
                self.cache.pop(key, None)
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self.cache.clear()
