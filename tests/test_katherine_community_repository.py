"""Tests for the dedicated community profile repository."""
from katherine_community_repository import CommunityRepository
import pytest
import main as katherine


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(katherine, "DB_PATH", tmp_path / "community.sqlite3")
    katherine.init_db()
    return katherine


def test_repository_upserts_profile_and_counts_activity(database):
    repo = CommunityRepository(db_session=database.db_session,
        activity_clock_bucket=lambda: ("2026-11-18", 12))
    repo.record_user("uid-1", "Viajero", count_message=True)
    repo.record_user_and_chat_activity("uid-1", "Viajero actualizado", "room")
    profile = repo.get_user("uid-1")
    assert profile["nickname"] == "Viajero actualizado"
    assert profile["message_count"] == 2


def test_profile_and_affinity_are_returned_in_one_result(database):
    repo = CommunityRepository(db_session=database.db_session,
        activity_clock_bucket=lambda: ("2026-11-18", 12))
    repo.record_user("viewer", "Viewer")
    repo.record_user("target", "Target")
    database.add_affinity("viewer", "target", cooldown=0)
    profile, affinity = repo.get_user_profile("target", "viewer")
    assert profile["nickname"] == "Target"
    assert affinity >= 0


def test_nickname_lookup_is_scoped_when_scope_is_supplied(database):
    repo = CommunityRepository(db_session=database.db_session,
        activity_clock_bucket=lambda: ("2026-11-18", 12))
    repo.record_user("room-a:user", "Viajero")
    repo.record_user("room-b:user", "Viajero")
    assert repo.find_user_by_nickname("Viajero", "room-a")["uid"] == "room-a:user"
    profile, _ = repo.find_user_profile_by_nickname("Viajero", "room-b", "viewer")
    assert profile["uid"] == "room-b:user"


def test_activity_record_without_room_does_not_create_room_counters(database):
    repo = CommunityRepository(db_session=database.db_session,
        activity_clock_bucket=lambda: ("2026-11-18", 12))
    repo.record_user_and_chat_activity("uid", "Viajero", "")
    with database.db_session() as db:
        daily = db.execute("SELECT COUNT(*) AS n FROM chat_activity_daily").fetchone()["n"]
        hourly = db.execute("SELECT COUNT(*) AS n FROM chat_activity_hourly").fetchone()["n"]
    assert daily == hourly == 0
