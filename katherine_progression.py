"""Daily progression, action quests and achievement tracking for Katheryne."""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any


ACTION_XP = {
    "daily_claim": 18,
    "work": 12,
    "trivia_correct": 15,
    "guess_win": 20,
    "community_event": 10,
}
DAILY_ACTION_CAP = 20
QUESTS = {
    "daily": {
        "title": "Constancia diaria",
        "description": "Reclama tu recompensa diaria.",
        "action": "daily_claim",
        "goal": 1,
        "bonus_xp": 20,
    },
    "work": {
        "title": "Jornada de expedición",
        "description": "Completa una jornada de trabajo.",
        "action": "work",
        "goal": 1,
        "bonus_xp": 15,
    },
    "quiz": {
        "title": "Conocimiento de Teyvat",
        "description": "Responde correctamente una trivia.",
        "action": "trivia_correct",
        "goal": 1,
        "bonus_xp": 25,
    },
    "guess": {
        "title": "Ojo de explorador",
        "description": "Adivina el número de una partida.",
        "action": "guess_win",
        "goal": 1,
        "bonus_xp": 30,
    },
}


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    uid: str
    xp: int
    level: int
    current_level_xp: int
    next_level_xp: int
    total_actions_today: int
    unlocked_achievements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuestSnapshot:
    quest_id: str
    title: str
    description: str
    progress: int
    goal: int
    bonus_xp: int
    claimed: bool
    completed: bool


def level_for_xp(xp: int) -> int:
    """A gently increasing curve; level one begins at zero experience."""
    return max(1, int(math.isqrt(max(0, int(xp)) // 100)) + 1)


def xp_floor_for_level(level: int) -> int:
    target = max(1, int(level))
    return (target - 1) ** 2 * 100


def xp_needed_for_next_level(level: int) -> int:
    target = max(1, int(level))
    return target ** 2 * 100


class ProgressionService:
    """Persistent, bounded game progression without retaining chat messages."""

    def __init__(
        self,
        *,
        db_session: Callable[[], Any],
        clock: Callable[[], float] = time.time,
        timezone: str = "America/Santiago",
        action_caps: dict[str, int] | None = None,
    ) -> None:
        self.db_session = db_session
        self.clock = clock
        self.timezone = ZoneInfo(timezone)
        self.action_caps = {
            key: max(1, min(100, int(value)))
            for key, value in (action_caps or {}).items()
        }

    def day_key(self) -> str:
        return datetime.fromtimestamp(self.clock(), self.timezone).date().isoformat()

    def _unlock_achievements(self, db: Any, uid: str, xp: int, level: int) -> None:
        thresholds = (
            ("first_steps", 1, 0),
            ("traveler_level_5", 5, 0),
            ("traveler_level_10", 10, 0),
            ("traveler_level_25", 25, 0),
            ("experienced_1000_xp", 1, 1000),
            ("master_5000_xp", 1, 5000),
        )
        now = float(self.clock())
        for key, required_level, required_xp in thresholds:
            if level >= required_level and xp >= required_xp:
                db.execute(
                    """INSERT OR IGNORE INTO community_achievements(
                           uid,achievement_id,unlocked_at
                       ) VALUES(?,?,?)""",
                    (str(uid), key, now),
                )

    def record_action(self, uid: str, action: str, amount: int = 1) -> int:
        """Record a whitelisted daily action and award XP under a strict cap."""
        key = str(action or "").strip().casefold()
        if key not in ACTION_XP:
            raise ValueError("Unknown progression action.")
        count = max(1, min(10, int(amount)))
        day = self.day_key()
        cap = self.action_caps.get(key, DAILY_ACTION_CAP)
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT action_count FROM community_daily_actions
                   WHERE uid=? AND day_key=? AND action=?""",
                (str(uid), day, key),
            ).fetchone()
            previous = int(row["action_count"]) if row else 0
            accepted = min(count, max(0, cap - previous))
            if accepted <= 0:
                progress = db.execute(
                    "SELECT xp FROM community_progress WHERE uid=?",
                    (str(uid),),
                ).fetchone()
                return int(progress["xp"]) if progress else 0
            total = previous + accepted
            db.execute(
                """INSERT INTO community_daily_actions(
                       uid,day_key,action,action_count,updated_at
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(uid,day_key,action) DO UPDATE SET
                       action_count=excluded.action_count,
                       updated_at=excluded.updated_at""",
                (str(uid), day, key, total, float(self.clock())),
            )
            new_xp = ACTION_XP[key] * accepted
            db.execute(
                """INSERT INTO community_progress(uid,xp,updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(uid) DO UPDATE SET
                       xp=community_progress.xp+excluded.xp,
                       updated_at=excluded.updated_at""",
                (str(uid), new_xp, float(self.clock())),
            )
            xp_row = db.execute(
                "SELECT xp FROM community_progress WHERE uid=?",
                (str(uid),),
            ).fetchone()
            xp = int(xp_row["xp"])
            self._unlock_achievements(db, str(uid), xp, level_for_xp(xp))
        return xp

    def snapshot(self, uid: str) -> ProgressSnapshot:
        day = self.day_key()
        with self.db_session() as db:
            row = db.execute(
                "SELECT xp FROM community_progress WHERE uid=?",
                (str(uid),),
            ).fetchone()
            actions = db.execute(
                """SELECT COALESCE(SUM(action_count),0) AS total
                   FROM community_daily_actions WHERE uid=? AND day_key=?""",
                (str(uid), day),
            ).fetchone()
            achievements = db.execute(
                """SELECT achievement_id FROM community_achievements
                   WHERE uid=? ORDER BY unlocked_at,achievement_id""",
                (str(uid),),
            ).fetchall()
        xp = int(row["xp"]) if row else 0
        level = level_for_xp(xp)
        floor = xp_floor_for_level(level)
        next_floor = xp_needed_for_next_level(level)
        return ProgressSnapshot(
            uid=str(uid), xp=xp, level=level,
            current_level_xp=xp - floor,
            next_level_xp=next_floor - floor,
            total_actions_today=int(actions["total"] if actions else 0),
            unlocked_achievements=tuple(str(item["achievement_id"]) for item in achievements),
        )

    def daily_quests(self, uid: str) -> list[QuestSnapshot]:
        day = self.day_key()
        with self.db_session() as db:
            action_rows = db.execute(
                """SELECT action,action_count FROM community_daily_actions
                   WHERE uid=? AND day_key=?""",
                (str(uid), day),
            ).fetchall()
            claimed_rows = db.execute(
                """SELECT quest_id FROM community_quest_claims
                   WHERE uid=? AND day_key=?""",
                (str(uid), day),
            ).fetchall()
        progress = {str(row["action"]): int(row["action_count"]) for row in action_rows}
        claimed = {str(row["quest_id"]) for row in claimed_rows}
        return [
            QuestSnapshot(
                quest_id=key,
                title=str(quest["title"]),
                description=str(quest["description"]),
                progress=min(int(quest["goal"]), progress.get(str(quest["action"]), 0)),
                goal=int(quest["goal"]),
                bonus_xp=int(quest["bonus_xp"]),
                claimed=key in claimed,
                completed=progress.get(str(quest["action"]), 0) >= int(quest["goal"]),
            )
            for key, quest in QUESTS.items()
        ]

    def claim_quest(self, uid: str, quest_id: str) -> ProgressSnapshot:
        key = str(quest_id or "").strip().casefold()
        quest = QUESTS.get(key)
        if quest is None:
            raise ValueError("Esa misión no existe.")
        day = self.day_key()
        now = float(self.clock())
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            action_row = db.execute(
                """SELECT action_count FROM community_daily_actions
                   WHERE uid=? AND day_key=? AND action=?""",
                (str(uid), day, str(quest["action"])),
            ).fetchone()
            if not action_row or int(action_row["action_count"]) < int(quest["goal"]):
                raise ValueError("Todavía no has completado esa misión.")
            exists = db.execute(
                """SELECT 1 FROM community_quest_claims
                   WHERE uid=? AND day_key=? AND quest_id=?""",
                (str(uid), day, key),
            ).fetchone()
            if exists:
                raise ValueError("Ya reclamaste esta misión hoy.")
            db.execute(
                """INSERT INTO community_quest_claims(uid,day_key,quest_id,claimed_at)
                   VALUES(?,?,?,?)""",
                (str(uid), day, key, now),
            )
            bonus = int(quest["bonus_xp"])
            db.execute(
                """INSERT INTO community_progress(uid,xp,updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(uid) DO UPDATE SET
                       xp=community_progress.xp+excluded.xp,
                       updated_at=excluded.updated_at""",
                (str(uid), bonus, now),
            )
            row = db.execute(
                "SELECT xp FROM community_progress WHERE uid=?",
                (str(uid),),
            ).fetchone()
            xp = int(row["xp"])
            self._unlock_achievements(db, str(uid), xp, level_for_xp(xp))
        return self.snapshot(uid)

    def leaderboard(
        self, *, limit: int = 10, scope_prefix: str = ""
    ) -> list[ProgressSnapshot]:
        count = max(1, min(50, int(limit)))
        sql = "SELECT uid,xp FROM community_progress"
        params: list[Any] = []
        if scope_prefix:
            sql += " WHERE substr(uid,1,?)=?"
            params.extend([len(scope_prefix), str(scope_prefix)])
        sql += " ORDER BY xp DESC,updated_at ASC LIMIT ?"
        params.append(count)
        with self.db_session() as db:
            rows = db.execute(sql, tuple(params)).fetchall()
        results = []
        for row in rows:
            xp = int(row["xp"])
            level = level_for_xp(xp)
            floor = xp_floor_for_level(level)
            next_floor = xp_needed_for_next_level(level)
            results.append(ProgressSnapshot(
                uid=str(row["uid"]), xp=xp, level=level,
                current_level_xp=xp-floor, next_level_xp=next_floor-floor,
                total_actions_today=0, unlocked_achievements=(),
            ))
        return results

    def purge_expired_daily_state(self, *, keep_days: int = 14) -> int:
        """Remove per-action quest counters and claims after a short retention window."""
        days = max(1, min(30, int(keep_days)))
        cutoff = datetime.fromtimestamp(
            self.clock(), self.timezone
        ).date().toordinal() - days
        cutoff_day = datetime.fromordinal(cutoff).isoformat()
        with self.db_session() as db:
            db.execute("BEGIN IMMEDIATE")
            actions = db.execute(
                "DELETE FROM community_daily_actions WHERE day_key<?",
                (cutoff_day,),
            ).rowcount
            claims = db.execute(
                "DELETE FROM community_quest_claims WHERE day_key<?",
                (cutoff_day,),
            ).rowcount
        return int(actions + claims)
