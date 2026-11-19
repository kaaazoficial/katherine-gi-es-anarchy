"""SQLite storage primitives shared by Katherine's runtime and maintenance tools.

Conversation history is intentionally never persisted by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator, Any


def has_persistent_storage(path: str | Path) -> bool:
    """Return True when path is beneath a mounted filesystem other than root."""
    candidate = Path(path).resolve()
    while candidate != candidate.parent and candidate != Path("/"):
        if os.path.ismount(candidate):
            return True
        candidate = candidate.parent
    return False


def connect_database(path: str | Path, timeout: float = 10.0) -> sqlite3.Connection:
    """Open a configured SQLite connection for the supplied database path."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def database_session(
    path: str | Path, timeout: float = 10.0
) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on failure, and always close the connection."""
    connection = connect_database(path, timeout=timeout)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify_sqlite_database(path: str | Path) -> dict[str, Any]:
    """Run SQLite's quick integrity check without creating a missing database."""
    database_path = Path(path)
    if not database_path.is_file():
        return {
            "ok": False,
            "integrity": "missing",
            "size_bytes": 0,
        }
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_path, timeout=5.0)
        row = connection.execute("PRAGMA quick_check").fetchone()
        integrity = str(row[0]) if row else "no result"
        return {
            "ok": integrity.casefold() == "ok",
            "integrity": integrity[:160],
            "size_bytes": database_path.stat().st_size,
        }
    except (OSError, sqlite3.Error) as exc:
        return {
            "ok": False,
            "integrity": type(exc).__name__,
            "size_bytes": database_path.stat().st_size if database_path.exists() else 0,
        }
    finally:
        if connection is not None:
            connection.close()


def create_sqlite_backup(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    pages: int = 256,
    sleep: float = 0.01,
) -> dict[str, Any]:
    """Create an atomic, transaction-consistent SQLite backup and verify it.

    Uses sqlite3.Connection.backup(), so committed WAL data is included and
    active writes are coordinated by SQLite. The destination is replaced only
    after the completed backup passes PRAGMA integrity_check.
    """
    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("Backup destination must differ from the source database.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    source_connection: sqlite3.Connection | None = None
    backup_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source, timeout=10.0)
        source_connection.execute("PRAGMA busy_timeout=10000")
        backup_connection = sqlite3.connect(temporary, timeout=10.0)
        source_connection.backup(
            backup_connection,
            pages=max(1, min(4096, int(pages))),
            sleep=max(0.0, min(1.0, float(sleep))),
        )
        row = backup_connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(row[0]) if row else "no result"
        if integrity.casefold() != "ok":
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity[:160]}")
        backup_connection.close()
        backup_connection = None
        source_connection.close()
        source_connection = None
        os.replace(temporary, destination)
        return {
            "path": str(destination),
            "size_bytes": destination.stat().st_size,
            "integrity": integrity,
        }
    finally:
        if backup_connection is not None:
            backup_connection.close()
        if source_connection is not None:
            source_connection.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
