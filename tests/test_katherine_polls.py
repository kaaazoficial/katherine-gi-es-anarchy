"""Persistent poll lifecycle, privacy and retention tests."""
import sqlite3
from contextlib import contextmanager
import pytest

from katherine_polls import (
    PollClosed, PollInvalid, PollNotFound, PollPermissionError, PollRepository,
)


@pytest.fixture
def polls(tmp_path):
    path = tmp_path / "polls.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE community_polls(
                poll_id TEXT PRIMARY KEY,code TEXT NOT NULL,source TEXT NOT NULL,
                thread_id TEXT NOT NULL,question TEXT NOT NULL,options_json TEXT NOT NULL,
                creator_uid TEXT NOT NULL,creator_name TEXT NOT NULL,
                anonymous INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,closes_at REAL NOT NULL,expires_at REAL NOT NULL,
                button_key TEXT NOT NULL UNIQUE
            );
            CREATE UNIQUE INDEX uq_poll_code_room
                ON community_polls(source,thread_id,code);
            CREATE TABLE community_poll_votes(
                poll_id TEXT NOT NULL,voter_key TEXT NOT NULL,option_index INTEGER NOT NULL,
                created_at REAL NOT NULL,PRIMARY KEY(poll_id,voter_key),
                FOREIGN KEY(poll_id) REFERENCES community_polls(poll_id) ON DELETE CASCADE
            );
        """)

    @contextmanager
    def session():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    clock = [1000.0]
    return PollRepository(db_session=session, clock=lambda: clock[0]), clock


def create(repository, **kwargs):
    values = {
        "poll_id": "community:room:poll-1", "code": "123456",
        "source": "community", "thread_id": "room", "question": "¿Cuál región?",
        "options": ["Mondstadt", "Liyue"], "creator_uid": "creator",
        "creator_name": "Creador", "anonymous": False, "duration_seconds": 3600,
        "button_key": "123456789",
    }
    values.update(kwargs)
    return repository.create(**values)


def test_create_vote_and_change_vote(polls):
    repository, _ = polls
    poll = create(repository)
    assert poll["votes"] == {}
    poll = repository.vote(poll["id"], "user-1", 0, source="community", thread_id="room")
    assert list(poll["votes"].values()) == [0]
    poll = repository.vote(poll["id"], "user-1", 1, source="community", thread_id="room")
    assert list(poll["votes"].values()) == [1]


def test_anonymous_vote_does_not_expose_raw_uid(polls):
    repository, _ = polls
    poll = create(repository, anonymous=True)
    updated = repository.vote(poll["id"], "platform-user-secret", 1,
                              source="community", thread_id="room")
    assert "platform-user-secret" not in updated["votes"]
    assert len(next(iter(updated["votes"]))) == 64


def test_poll_is_scoped_to_room_and_source(polls):
    repository, _ = polls
    poll = create(repository)
    assert repository.get(poll["id"], source="community", thread_id="room")
    assert repository.get(poll["id"], source="other", thread_id="room") is None
    with pytest.raises(PollNotFound):
        repository.vote(poll["id"], "u", 0, source="other", thread_id="room")


def test_close_requires_creator_or_admin(polls):
    repository, _ = polls
    poll = create(repository)
    with pytest.raises(PollPermissionError):
        repository.close(poll["id"], "other", source="community", thread_id="room")
    closed = repository.close(poll["id"], "admin", source="community",
                              thread_id="room", is_admin=True)
    assert closed["closed"]


def test_expiry_blocks_votes_and_purges_after_retention(polls):
    repository, clock = polls
    poll = create(repository, duration_seconds=60)
    clock[0] = 1061
    assert repository.close_expired() == 1
    with pytest.raises(PollClosed):
        repository.vote(poll["id"], "user", 0, source="community", thread_id="room")
    clock[0] = 1061 + 86400
    assert repository.purge_expired() == {"polls": 1, "votes": 0}
    assert repository.get(poll["id"]) is None


def test_invalid_poll_input_is_rejected(polls):
    repository, _ = polls
    with pytest.raises(PollInvalid):
        create(repository, options=["Only one"])
    with pytest.raises(PollInvalid):
        create(repository, duration_seconds=10)
