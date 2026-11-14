"""Regression tests for safe SQLite backup and connection primitives."""
from __future__ import annotations

import sqlite3

import pytest

from katherine_storage import (
    create_sqlite_backup,
    database_session,
    verify_sqlite_database,
)


def test_backup_is_consistent_and_passes_integrity_check(tmp_path):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "exports" / "backup.sqlite3"
    with database_session(source) as db:
        db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO sample(value) VALUES ('committed WAL data')")

    result = create_sqlite_backup(source, destination)

    assert result["integrity"] == "ok"
    assert result["size_bytes"] > 0
    assert verify_sqlite_database(destination)["ok"] is True
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT value FROM sample").fetchone()[0] == "committed WAL data"


def test_backup_does_not_replace_destination_when_source_missing(tmp_path):
    destination = tmp_path / "backup.sqlite3"
    destination.write_bytes(b"keep this")
    with pytest.raises(FileNotFoundError):
        create_sqlite_backup(tmp_path / "missing.sqlite3", destination)
    assert destination.read_bytes() == b"keep this"


def test_backup_rejects_same_source_and_destination(tmp_path):
    source = tmp_path / "source.sqlite3"
    with database_session(source) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
    with pytest.raises(ValueError, match="differ"):
        create_sqlite_backup(source, source)


def test_integrity_check_does_not_create_missing_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    result = verify_sqlite_database(missing)
    assert result["ok"] is False
    assert result["integrity"] == "missing"
    assert not missing.exists()
