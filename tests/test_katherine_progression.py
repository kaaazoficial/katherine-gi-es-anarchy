"""Progression and daily quest tests with a deterministic clock."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
import pytest

from katherine_progression import (
    ProgressionService, level_for_xp, xp_floor_for_level,
    xp_needed_for_next_level,
)


@pytest.fixture
def progression(tmp_path):
    path = tmp_path / "progress.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE community_progress(
                uid TEXT PRIMARY KEY,xp INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            CREATE TABLE community_daily_actions(
                uid TEXT NOT NULL,day_key TEXT NOT NULL,action TEXT NOT NULL,
                action_count INTEGER NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(uid,day_key,action)
            );
            CREATE TABLE community_quest_claims(
                uid TEXT NOT NULL,day_key TEXT NOT NULL,quest_id TEXT NOT NULL,
                claimed_at REAL NOT NULL,PRIMARY KEY(uid,day_key,quest_id)
            );
            CREATE TABLE community_achievements(
                uid TEXT NOT NULL,achievement_id TEXT NOT NULL,unlocked_at REAL NOT NULL,
                PRIMARY KEY(uid,achievement_id)
            );
        """)

    @contextmanager
    def session():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    clock = [datetime(2026, 11, 18, 12, tzinfo=timezone.utc).timestamp()]
    service = ProgressionService(
        db_session=session, clock=lambda: clock[0], timezone="UTC",
        action_caps={"work": 2},
    )
    return service, clock


def test_level_curve_is_monotonic_and_has_expected_thresholds():
    assert level_for_xp(0) == 1
    assert level_for_xp(399) == 2
    assert level_for_xp(400) == 3
    assert xp_floor_for_level(3) == 400
    assert xp_needed_for_next_level(3) == 900


def test_record_action_awards_xp_and_caps_daily_farming(progression):
    service, _ = progression
    assert service.record_action("user", "work", amount=3) == 24
    assert service.record_action("user", "work", amount=3) == 24
    snapshot = service.snapshot("user")
    assert snapshot.xp == 24
    assert snapshot.total_actions_today == 2


def test_daily_quests_progress_and_claim_once(progression):
    service, _ = progression
    service.record_action("user", "daily_claim")
    quest = next(item for item in service.daily_quests("user") if item.quest_id == "daily")
    assert quest.completed and not quest.claimed
    snapshot = service.claim_quest("user", "daily")
    assert snapshot.xp == 38
    assert next(item for item in service.daily_quests("user") if item.quest_id == "daily").claimed
    with pytest.raises(ValueError, match="Ya reclamaste"):
        service.claim_quest("user", "daily")


def test_quest_cannot_be_claimed_before_completion(progression):
    service, _ = progression
    with pytest.raises(ValueError, match="no has completado"):
        service.claim_quest("user", "quiz")


def test_achievements_unlock_on_level_thresholds(progression):
    service, _ = progression
    for _ in range(20):
        service.record_action("user", "guess_win")
    snapshot = service.snapshot("user")
    assert snapshot.level >= 3
    assert "first_steps" in snapshot.unlocked_achievements


def test_daily_actions_reset_at_local_midnight(progression):
    service, clock = progression
    service.record_action("user", "daily_claim")
    before = service.snapshot("user")
    clock[0] += 86400
    after = service.snapshot("user")
    assert before.total_actions_today == 1
    assert after.total_actions_today == 0
    assert after.xp == before.xp


def test_unknown_actions_and_quests_are_rejected(progression):
    service, _ = progression
    with pytest.raises(ValueError, match="Unknown"):
        service.record_action("user", "raw_message")
    with pytest.raises(ValueError, match="no existe"):
        service.claim_quest("user", "not-a-quest")


def test_progression_leaderboard_is_bounded(progression):
    service, _ = progression
    service.record_action("a", "daily_claim")
    service.record_action("b", "work")
    top = service.leaderboard(limit=1)
    assert len(top) == 1
    assert top[0].uid == "a"


def test_progression_leaderboard_is_room_scoped(progression):
    service, _ = progression
    service.record_action("community:room:a", "daily_claim")
    service.record_action("community:other:b", "guess_win")
    top = service.leaderboard(scope_prefix="community:room:")
    assert [item.uid for item in top] == ["community:room:a"]
