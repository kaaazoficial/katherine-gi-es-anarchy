"""Regression tests for privacy-first retention of ephemeral/user-linked state."""
import sqlite3
import time
from datetime import date

import pytest

from katherine_retention import cleanup_database_retention


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE users (
            uid TEXT PRIMARY KEY, nickname TEXT, bio TEXT DEFAULT '',
            status TEXT DEFAULT '', points INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0, created_at REAL,
            last_seen REAL, profile_updated_at REAL
        );
        CREATE TABLE affinities (uid TEXT, target_uid TEXT, score INTEGER, last_at REAL);
        CREATE TABLE gifts (
            id INTEGER PRIMARY KEY, sender_uid TEXT, sender_name TEXT,
            target_uid TEXT, target_name TEXT, message TEXT, created_at REAL
        );
        CREATE TABLE wish_state (uid TEXT);
        CREATE TABLE reward_claims (uid TEXT, action TEXT, claimed_at REAL);
        CREATE TABLE moderation_warnings (
            id INTEGER PRIMARY KEY, target_uid TEXT, moderator_uid TEXT,
            created_at REAL
        );
        CREATE TABLE dj_playlists (id INTEGER PRIMARY KEY, created_by TEXT, created_at REAL);
        CREATE TABLE auto_reply_rules (id INTEGER PRIMARY KEY, created_by TEXT, created_at REAL);
        CREATE TABLE chat_activity_daily (thread_id TEXT, uid TEXT, day_key TEXT);
        CREATE TABLE chat_activity_hourly (thread_id TEXT, day_key TEXT);
        CREATE TABLE admin_audit_log (id INTEGER PRIMARY KEY, created_at REAL);
        CREATE TABLE translation_daily_usage (day_key TEXT);
    """)
    return db


@pytest.fixture
def db():
    connection = make_db()
    try:
        yield connection
    finally:
        connection.close()


def test_retention_expires_profile_text_and_inactive_user_state(db):
    now = time.time()
    db.execute(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)",
        ("active", "Ana", "bio antigua", "estado antiguo", 100, 4,
         now - 100 * 86400, now - 10 * 86400, now - 31 * 86400),
    )
    db.execute(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)",
        ("inactive", "Luis", "", "", 50, 2,
         now - 200 * 86400, now - 91 * 86400, None),
    )
    db.execute("INSERT INTO affinities VALUES('inactive','active',4,?)", (now,))
    db.execute("INSERT INTO wish_state VALUES('inactive')")
    db.execute("INSERT INTO reward_claims VALUES('inactive','daily',?)", (now,))
    db.execute("INSERT INTO gifts VALUES(1,'inactive','Luis','active','Ana','hola',?)", (now,))
    db.commit()

    counts = cleanup_database_retention(db, now=now, today=date(2026, 11, 12))

    active = db.execute("SELECT * FROM users WHERE uid='active'").fetchone()
    assert active["bio"] == ""
    assert active["status"] == ""
    assert db.execute("SELECT 1 FROM users WHERE uid='inactive'").fetchone() is None
    assert db.execute("SELECT COUNT(*) FROM affinities").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM wish_state").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM reward_claims").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM gifts").fetchone()[0] == 0
    assert counts["profile_texts_expired"] == 1
    assert counts["inactive_users_deleted"] == 1


def test_retention_keeps_recent_records_but_prunes_old_activity_and_audits(db):
    now = time.time()
    db.execute("INSERT INTO users VALUES('u','Ana','','',0,0,?,?,NULL)", (now, now))
    db.execute("INSERT INTO gifts VALUES(1,'u','Ana','u','Ana','reciente',?)", (now,))
    db.execute("INSERT INTO chat_activity_daily VALUES('room','u','2026-11-12')")
    db.execute("INSERT INTO chat_activity_daily VALUES('room','u','2026-11-01')")
    db.execute("INSERT INTO chat_activity_hourly VALUES('room','2026-11-01')")
    db.execute("INSERT INTO admin_audit_log VALUES(1,?)", (now - 31 * 86400,))
    db.execute("INSERT INTO admin_audit_log VALUES(2,?)", (now,))
    db.execute("INSERT INTO translation_daily_usage VALUES('2026-11-11')")
    db.execute("INSERT INTO translation_daily_usage VALUES('2026-11-12')")
    db.commit()

    cleanup_database_retention(db, now=now, today=date(2026, 11, 12))

    assert db.execute("SELECT COUNT(*) FROM gifts").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM chat_activity_daily").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM chat_activity_hourly").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM admin_audit_log").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM translation_daily_usage").fetchone()[0] == 1


def test_retention_anonymizes_old_playlist_and_auto_reply_creators(db):
    now = time.time()
    db.execute("INSERT INTO dj_playlists VALUES(1,'uid-123',?)", (now - 91 * 86400,))
    db.execute("INSERT INTO auto_reply_rules VALUES(1,'uid-456',?)", (now - 91 * 86400,))
    db.commit()

    cleanup_database_retention(db, now=now, today=date(2026, 11, 12))

    assert db.execute("SELECT created_by FROM dj_playlists").fetchone()[0] == ""
    assert db.execute("SELECT created_by FROM auto_reply_rules").fetchone()[0] == ""
