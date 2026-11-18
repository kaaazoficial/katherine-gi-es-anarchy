"""Tests for room-scoped settings persistence and cache behavior."""
import sqlite3
from contextlib import contextmanager
from collections import defaultdict
from katherine_settings import ChatSettingsStore


def make_store(clock=None, max_entries=10):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE chat_settings(
        thread_id TEXT, setting_key TEXT, setting_value TEXT, updated_at REAL,
        PRIMARY KEY(thread_id, setting_key))""")
    @contextmanager
    def session():
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    now = [10.0]
    store = ChatSettingsStore(db_session=session, metrics=defaultdict(int),
        ttl_seconds=5, max_entries=max_entries,
        clock=clock or (lambda: now[0]))
    return store, connection, now


def test_setting_round_trip_and_default_cache():
    store, db, _ = make_store()
    assert store.get("room", "language", "es") == "es"
    store.set("room", "language", "pt")
    assert store.get("room", "language") == "pt"
    assert store.get("other-room", "language", "es") == "es"


def test_get_many_batches_missing_keys_and_preserves_defaults():
    store, db, _ = make_store()
    store.set("room", "language", "es")
    values = store.get_many("room", {"language": "en", "tts": "off"})
    assert values == {"language": "es", "tts": "off"}
    assert store.get_many("room", {"language": "en", "tts": "off"}) == values


def test_cache_expires_and_reads_fresh_database_value():
    store, db, clock = make_store()
    store.set("room", "language", "es")
    assert store.get("room", "language") == "es"
    db.execute("UPDATE chat_settings SET setting_value='fr'")
    clock[0] += 6
    assert store.get("room", "language") == "fr"


def test_cache_enforces_capacity_and_prunes_expired_entries():
    store, _, clock = make_store(max_entries=2)
    store.set("r", "a", "1")
    store.set("r", "b", "2")
    store.set("r", "c", "3")
    assert len(store.cache) == 2
    clock[0] += 6
    assert store.prune() == 2
    assert len(store.cache) == 0


def test_room_settings_are_isolated_between_rooms():
    store, _, _ = make_store()
    store.set("one", "mode", "public")
    store.set("two", "mode", "private")
    assert store.get("one", "mode") == "public"
    assert store.get("two", "mode") == "private"
