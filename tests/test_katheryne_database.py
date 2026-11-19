"""Tests for the isolated SQLite lifecycle."""
import sqlite3

import pytest

from katherine_database import connect_database, database_session


def test_database_session_commits_and_closes_connections(tmp_path):
    path = tmp_path / "katherine.sqlite3"
    opened = []

    def connect():
        connection = connect_database(path)
        opened.append(connection)
        return connection

    with database_session(connect) as db:
        db.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
        db.execute("INSERT INTO sample (value) VALUES (7)")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")

    with sqlite3.connect(path) as check:
        assert check.execute("SELECT value FROM sample").fetchone() == (7,)


def test_database_session_rolls_back_and_closes_on_error(tmp_path):
    path = tmp_path / "katherine.sqlite3"
    opened = []

    def connect():
        connection = connect_database(path)
        opened.append(connection)
        return connection

    with sqlite3.connect(path) as setup:
        setup.execute("CREATE TABLE sample (value INTEGER NOT NULL)")

    with pytest.raises(RuntimeError, match="forced failure"):
        with database_session(connect) as db:
            db.execute("INSERT INTO sample (value) VALUES (9)")
            raise RuntimeError("forced failure")

    with sqlite3.connect(path) as check:
        assert check.execute("SELECT COUNT(*) FROM sample").fetchone() == (0,)
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
