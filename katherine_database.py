"""SQLite connection lifecycle and schema bootstrap for Katherine."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


def has_persistent_storage(path: Path) -> bool:
    """Return True only when the data path sits beneath a non-root mount point."""
    candidate = Path(path).resolve()
    while candidate != candidate.parent and candidate != Path("/"):
        if os.path.ismount(candidate):
            return True
        candidate = candidate.parent
    return False


def connect_database(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with row objects and safe per-connection pragmas."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def database_session(connect: Callable[[], sqlite3.Connection]) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error and always close the connection."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(session_factory):
    with session_factory() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            points INTEGER NOT NULL DEFAULT 0,
            message_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            profile_updated_at REAL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS affinities (
            uid TEXT NOT NULL,
            target_uid TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            last_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(uid, target_uid)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_uid TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            target_uid TEXT NOT NULL,
            target_name TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS wish_state (
            uid TEXT PRIMARY KEY,
            pity5 INTEGER NOT NULL DEFAULT 0,
            pity4 INTEGER NOT NULL DEFAULT 0,
            guaranteed INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS reward_claims (
            uid TEXT NOT NULL,
            action TEXT NOT NULL,
            claimed_at REAL NOT NULL,
            PRIMARY KEY(uid, action)
        )""")
        # Retira tablas de funciones personales que ya no forman parte del bot.
        for obsolete_table in (
            "saved_builds",
            "saved_teams",
            "farm_materials",
            "exploration_progress",
            "genshin_notes"
        ):
            db.execute(f"DROP TABLE IF EXISTS {obsolete_table}")

        # Elimina también la columna UID heredada de Enka, si todavía existe.
        user_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "genshin_uid" in user_columns:
            try:
                db.execute("ALTER TABLE users DROP COLUMN genshin_uid")
            except sqlite3.OperationalError:
                # Compatibilidad con SQLite antiguo: deja el campo vacío y sin uso.
                db.execute("UPDATE users SET genshin_uid=''")

        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "profile_updated_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN profile_updated_at REAL")
            db.execute("UPDATE users SET profile_updated_at=last_seen WHERE bio<>'' OR status<>''")

        db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_gifts_target ON gifts(target_uid, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)")
        # Lifetime message counts are not used; clear values left by older builds.
        db.execute("UPDATE users SET message_count=0 WHERE message_count<>0")
        db.execute("DROP INDEX IF EXISTS idx_users_activity")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_nickname_last_seen ON users(lower(nickname), last_seen DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS chat_settings (
            thread_id TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(thread_id, setting_key)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS moderation_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            target_uid TEXT NOT NULL,
            target_name TEXT NOT NULL,
            moderator_uid TEXT NOT NULL,
            moderator_name TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            automatic INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            removed_at REAL,
            removed_by TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_warnings_target ON moderation_warnings(thread_id,target_uid,active,id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_warnings_target_uid ON moderation_warnings(target_uid)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_warnings_moderator_uid ON moderation_warnings(moderator_uid)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_warnings_created_at ON moderation_warnings(created_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS dj_playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(thread_id, name_key)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS dj_playlist_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(playlist_id) REFERENCES dj_playlists(id) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_playlist_tracks ON dj_playlist_tracks(playlist_id,id)")

        db.execute("""CREATE TABLE IF NOT EXISTS chat_activity_daily (
            thread_id TEXT NOT NULL,
            uid TEXT NOT NULL,
            nickname TEXT NOT NULL,
            day_key TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(thread_id, uid, day_key)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_daily_day ON chat_activity_daily(thread_id,day_key)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_daily_retention ON chat_activity_daily(day_key)")
        db.execute("""CREATE TABLE IF NOT EXISTS chat_activity_hourly (
            thread_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            hour_of_day INTEGER NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(thread_id, day_key, hour_of_day)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_hourly_retention ON chat_activity_hourly(day_key)")
        db.execute("""CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            actor_uid TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_thread ON admin_audit_log(thread_id,id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at ON admin_audit_log(created_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS auto_reply_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            trigger_phrase TEXT NOT NULL,
            response_text TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(thread_id, trigger_phrase)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS translation_daily_usage (
            day_key TEXT PRIMARY KEY,
            characters_used INTEGER NOT NULL DEFAULT 0
        )""")
